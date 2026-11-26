import asyncio
import logging

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_LW_AUTH_METHOD,
    CONF_LW_HUB_SERIALS,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
    DOMAIN,
)
from .runtime import LightwaveRuntimeData, get_runtime
from .services import async_setup_services
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ConfigEntryState,
)
from homeassistant.const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import (
    config_entry_oauth2_flow,
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.loader import async_get_integration

from .utils import get_stored_tokens, set_stored_tokens

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

# Define supported platforms
PLATFORMS_FIRMWARE = ["update"]
PLATFORMS = ["switch", "light", "climate", "cover", "binary_sensor", "sensor", "lock", "event", "select"]


def async_central_callback(**kwargs):
    _LOGGER.debug("Central callback")


# Reconnect retries every 30 seconds. Wait longer than a deploy blip before a repair.
_LINK_REPAIR_DELAY = 120


def _link_issue_id(entry_id):
    return f"{entry_id}_link_disconnected"


def _clear_link_issue(hass, entry_id):
    ir.async_delete_issue(hass, DOMAIN, _link_issue_id(entry_id))


def duplicate_issue_id(entry_id):
    return f"{entry_id}_duplicate_account"


def _hub_identifiers(link):
    return {(DOMAIN, featureset_id) for featureset_id, _name in link.get_hubs()}


def _entry_has_hub(device_registry, entry, hub_identifiers):
    return any(
        device.identifiers & hub_identifiers
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    )


def _earlier_owner(hass, config_entry, link):
    """Return an older entry that already has a hub from this login.

    The same login added twice has the same hub identifiers. The older entry
    keeps them.
    """
    hub_identifiers = _hub_identifiers(link)
    device_registry = dr.async_get(hass)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == config_entry.entry_id:
            break
        if entry.source == SOURCE_IGNORE or entry.disabled_by is not None:
            continue
        if _entry_has_hub(device_registry, entry, hub_identifiers):
            return entry
    return None


def _later_duplicates(hass, config_entry, link):
    """Return newer entries that registered a hub from this login and are not running.

    A disabled entry is never set up, so it cannot find its owner itself. A
    running newer entry started while this one was disabled. The user has been
    using it, so it is not offered for removal.
    """
    hub_identifiers = _hub_identifiers(link)
    device_registry = dr.async_get(hass)
    duplicates = []
    later = False
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == config_entry.entry_id:
            later = True
            continue
        if (
            not later
            or entry.source == SOURCE_IGNORE
            or entry.state is ConfigEntryState.LOADED
        ):
            continue
        if _entry_has_hub(device_registry, entry, hub_identifiers):
            duplicates.append(entry)
    return duplicates


def _raise_duplicate_issue(hass, entry, owner):
    ir.async_create_issue(
        hass,
        DOMAIN,
        duplicate_issue_id(entry.entry_id),
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="duplicate_account",
        translation_placeholders={
            "entry_title": entry.title,
            "owner_title": owner.title,
        },
        data={"entry_id": entry.entry_id, "owner_entry_id": owner.entry_id},
    )


def _record_hub_serials(hass, config_entry, link):
    """Store hub serials so discovery can tell this login's hubs apart."""
    serials = []
    for featureset_id, _name in link.get_hubs():
        serial = getattr(link.featuresets[featureset_id].device, "serial", None)
        if isinstance(serial, str) and serial.strip() and serial.strip() not in serials:
            serials.append(serial.strip())
    if serials and config_entry.data.get(CONF_LW_HUB_SERIALS) != serials:
        hass.config_entries.async_update_entry(
            config_entry, data={**config_entry.data, CONF_LW_HUB_SERIALS: serials}
        )


def _cancel_link_repair_delay(hass, entry_id):
    config_entry = hass.config_entries.async_get_entry(entry_id)
    if config_entry is None:
        return
    runtime = get_runtime(config_entry)
    if runtime is None or runtime.link_repair_unsub is None:
        return
    runtime.link_repair_unsub()
    runtime.link_repair_unsub = None


def _schedule_link_repair(hass, entry_id):
    """Raise a repair only if this drop is still down after the reconnect window."""
    config_entry = hass.config_entries.async_get_entry(entry_id)
    if config_entry is None:
        return
    runtime = get_runtime(config_entry)
    if runtime is None or runtime.link_repair_unsub is not None:
        return

    @callback
    def _raise_if_still_down(_now):
        current = get_runtime(config_entry)
        if current is None:
            return
        current.link_repair_unsub = None
        link = current.link
        if link is not None and link.connected:
            return
        ir.async_create_issue(
            hass,
            DOMAIN,
            _link_issue_id(entry_id),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="link_disconnected",
        )

    runtime.link_repair_unsub = async_call_later(
        hass, _LINK_REPAIR_DELAY, _raise_if_still_down
    )


def _refresh_entity_availability(runtime):
    """Write state so entities pick up the link's connected flag."""
    if runtime is None:
        return
    for entity in list(runtime.entities):
        if getattr(entity, "hass", None) is not None:
            entity.async_write_ha_state()


async def _safe_deactivate_link(link, source: str) -> None:
    """Deactivate a Lightwave link without raising during error/unload cleanup."""
    if link is None:
        return
    try:
        await link.async_deactivate(source)
        _LOGGER.debug("Deactivated Lightwave link (%s)", source)
    except Exception as err:
        _LOGGER.error("Error deactivating Lightwave link (%s): %s", source, err)


def _classify_connect_failure(
    link,
    auth_method: str,
    had_access_token: bool,
    err: Exception | None = None,
) -> Exception:
    """Classify a setup connection failure as AuthFailed or NotReady."""
    if isinstance(err, (ConfigEntryAuthFailed, ConfigEntryNotReady)):
        return err
    if isinstance(err, config_entry_oauth2_flow.OAuth2TokenRequestReauthError):
        return ConfigEntryAuthFailed(f"Authentication failed: {err}")

    auth = getattr(link, "auth", None)
    if auth_method == "oauth":
        last_error = getattr(auth, "last_error", None)
        if isinstance(last_error, config_entry_oauth2_flow.OAuth2TokenRequestReauthError):
            return ConfigEntryAuthFailed(f"OAuth authentication failed: {last_error}")
        if getattr(auth, "token_rejected", False):
            return ConfigEntryAuthFailed(
                "Lightwave rejected the OAuth token. Please reauthenticate."
            )
        detail = err or last_error or "Unable to connect to Lightwave service"
        return ConfigEntryNotReady(f"Temporary error connecting to Lightwave: {detail}")

    tokens = auth.get_tokens() if auth is not None and hasattr(auth, "get_tokens") else {}
    if auth_method in ("refresh", "api_key"):
        if not tokens.get("refresh_token"):
            return ConfigEntryAuthFailed(
                "Lightwave refresh token is invalid or expired. Please reauthenticate."
            )
        detail = err or "Unable to connect to Lightwave service"
        return ConfigEntryNotReady(f"Temporary error connecting to Lightwave: {detail}")

    # For password auth, LWAuth clears access_token only after connecting to the
    # websocket and receiving an explicit auth rejection from Lightwave.
    if had_access_token and not tokens.get("access_token"):
        return ConfigEntryAuthFailed(
            "Failed to authenticate with Lightwave. Please check your credentials."
        )

    detail = err or "Unable to connect to Lightwave service"
    return ConfigEntryNotReady(f"Temporary error connecting to Lightwave: {detail}")


async def async_setup(hass, config):
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    entry_id = config_entry.entry_id
    auth_method = config_entry.data.get(CONF_LW_AUTH_METHOD) or "password"

    link = await setup_link_lw(hass, config_entry)
    had_access_token = (
        auth_method != "oauth"
        and bool(link.auth.get_tokens().get("access_token"))
    )

    def _on_link_disconnect():
        runtime = get_runtime(config_entry)
        if runtime is not None and not runtime.unavailable_logged:
            _LOGGER.info("Lightwave connection lost")
            runtime.unavailable_logged = True
        _schedule_link_repair(hass, entry_id)
        _refresh_entity_availability(runtime)

    def _on_link_connect():
        runtime = get_runtime(config_entry)
        if runtime is not None and runtime.unavailable_logged:
            _LOGGER.info("Lightwave connection restored")
            runtime.unavailable_logged = False
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        _refresh_entity_availability(runtime)

    link.register_disconnect_callback(_on_link_disconnect)
    link.register_connect_callback(_on_link_connect)

    try:
        connected = await link.async_activate(
            max_tries=2,
            source="hass",
            connect_callback=link.async_get_hierarchy,
        )
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        await _safe_deactivate_link(link, "setup_cancelled")
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        raise
    except Exception as err:
        _LOGGER.error("Error connecting to Lightwave: %s", err)
        await _safe_deactivate_link(link, "setup_failed")
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        raise _classify_connect_failure(
            link, auth_method, had_access_token, err
        ) from err

    if not connected:
        await _safe_deactivate_link(link, "setup_not_connected")
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        raise _classify_connect_failure(link, auth_method, had_access_token)

    owner = _earlier_owner(hass, config_entry, link)
    if owner is not None:
        await _safe_deactivate_link(link, "setup_duplicate")
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        _raise_duplicate_issue(hass, config_entry, owner)
        raise ConfigEntryError(
            f"This Lightwave login is already set up as '{owner.title}'"
        )
    ir.async_delete_issue(hass, DOMAIN, duplicate_issue_id(entry_id))
    for duplicate in _later_duplicates(hass, config_entry, link):
        _raise_duplicate_issue(hass, duplicate, config_entry)
    _record_hub_serials(hass, config_entry, link)

    runtime = LightwaveRuntimeData(link=link)
    config_entry.runtime_data = runtime

    try:
        device_registry = dr.async_get(hass)
        entity_registry = er.async_get(hass)
        for featureset_id, hubname in link.get_hubs():
            structure_name = link.get_structure_name(featureset_id)
            if structure_name is not None:
                hubname = f"{hubname} {structure_name}"

            device_registry.async_get_or_create(
                config_entry_id=entry_id,
                configuration_url="https://my.lightwaverf.com/a/login",
                entry_type=dr.DeviceEntryType.SERVICE,
                identifiers={(DOMAIN, featureset_id)},
                manufacturer="Lightwave",
                name=hubname,
                model=link.featuresets[featureset_id].product_code,
            )

        remove_missing_devices_and_entities(
            config_entry, link, device_registry, entity_registry
        )

        all_platforms = [*PLATFORMS_FIRMWARE, *PLATFORMS]
        await hass.config_entries.async_forward_entry_setups(
            config_entry, all_platforms
        )
        runtime.platforms.extend(all_platforms)
    except BaseException:
        await _safe_deactivate_link(link, "setup_platform_failed")
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        raise

    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, source: str = "unload"
) -> bool:
    """Unload a config entry."""
    entry_id = config_entry.entry_id
    _LOGGER.info("Unloading config entry: '%s' from %s", entry_id, source)

    runtime = get_runtime(config_entry)
    if runtime is None:
        _LOGGER.warning("Config entry '%s' was never loaded, skipping unload", entry_id)
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        return True

    loaded_platforms = list(runtime.platforms)
    unload_ok = True
    try:
        if loaded_platforms:
            try:
                unload_ok = await hass.config_entries.async_unload_platforms(
                    config_entry, loaded_platforms
                )
            except Exception as err:
                _LOGGER.warning(
                    "Error unloading platforms for config entry '%s': %s",
                    entry_id,
                    err,
                )
                unload_ok = False
        else:
            _LOGGER.warning("No platforms were loaded for config entry '%s'", entry_id)
    finally:
        if runtime.link is not None:
            await _safe_deactivate_link(runtime.link, "async_unload_entry")

        # A drop while platforms unload can start a repair. Cancel that wait and
        # delete any repair once the link has been stopped.
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)

    _LOGGER.info(
        "Finished unloading config entry: '%s' (ok=%s, platforms=%s)",
        entry_id,
        unload_ok,
        len(loaded_platforms),
    )
    return unload_ok


async def async_remove_entry(hass, config_entry):
    """Remove a config entry - this is called when the integration is removed."""
    _LOGGER.debug(f"Removing config entry: {config_entry.entry_id}")
    _cancel_link_repair_delay(hass, config_entry.entry_id)
    _clear_link_issue(hass, config_entry.entry_id)
    ir.async_delete_issue(hass, DOMAIN, duplicate_issue_id(config_entry.entry_id))


async def setup_link_lw(hass, config_entry):
    from lightwave_smart import lightwave_smart

    integration = await async_get_integration(hass, DOMAIN)
    client_version = integration.version

    auth_method = config_entry.data.get(CONF_LW_AUTH_METHOD) or "password"
    username = config_entry.data.get(CONF_USERNAME) or None
    password = config_entry.data.get(CONF_PASSWORD) or None

    tokens = await get_stored_tokens(hass, username) if username else {}
    api_key = tokens.get(CONF_API_KEY)
    access_token = tokens.get(CONF_ACCESS_TOKEN)
    refresh_token = tokens.get(CONF_REFRESH_TOKEN)
    token_expiry = tokens.get(CONF_TOKEN_EXPIRY)

    log_data = {
        "auth_method": auth_method,
        "username": username,
        "password": "yes" if password else "no",
        "api_key": api_key[:1] + "..." + api_key[-1:] if api_key else "no",
        "access_token": access_token[:5] + "..." + access_token[-5:] if access_token else "no",
        "refresh_token": refresh_token[:1] + "..." + refresh_token[-1:] if refresh_token else "no",
        "token_expiry": token_expiry if token_expiry else "no",
    }
    _LOGGER.info(f"Setting up Lightwave link, config entry data: {log_data}")

    if auth_method == "oauth":
        from .auth import LightwaveSmartAuth, async_register_cloud_account_link_provider

        if CONF_TOKEN not in config_entry.data:
            raise ConfigEntryAuthFailed("Missing OAuth token in configuration entry.")

        async_register_cloud_account_link_provider(hass)
        try:
            implementation = (
                await config_entry_oauth2_flow.async_get_config_entry_implementation(
                    hass, config_entry
                )
            )
        except config_entry_oauth2_flow.ImplementationUnavailableError as err:
            raise ConfigEntryNotReady(
                f"OAuth implementation unavailable: {err}"
            ) from err

        session = config_entry_oauth2_flow.OAuth2Session(hass, config_entry, implementation)

        lightwave_smart_auth = LightwaveSmartAuth(session)
        link = lightwave_smart.LWLink2(
            auth=lightwave_smart_auth, client_version=client_version
        )

    else:
        if auth_method == "password" and (not username or not password):
            raise ConfigEntryAuthFailed("Missing Lightwave username or password.")
        if auth_method == "refresh" and (not username or not refresh_token):
            raise ConfigEntryAuthFailed(
                "Missing Lightwave refresh token. Please reauthenticate."
            )
        if auth_method == "api_key" and (not api_key or not refresh_token):
            raise ConfigEntryAuthFailed("Missing Lightwave API key or refresh token.")

        def on_token_refresh(access_token, refresh_token, token_expiry):
            _LOGGER.info("Updating tokens in storage")
            asyncio.create_task(
                set_stored_tokens(
                    hass=hass,
                    username=username,
                    tokens={
                        CONF_ACCESS_TOKEN: access_token,
                        CONF_REFRESH_TOKEN: refresh_token,
                        CONF_TOKEN_EXPIRY: token_expiry,
                    },
                )
            )

        link = lightwave_smart.LWLink2(client_version=client_version)
        link.auth.set_token_refresh_callback(on_token_refresh)

        if auth_method == "password":
            link.auth.set_auth_method(
                auth_method=auth_method,
                username=username,
                password=password,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=token_expiry,
            )
        else:
            link.auth.set_auth_method(
                auth_method=auth_method,
                api_key=api_key,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expiry=token_expiry,
            )

    link.hass = hass
    link.config_entry_id = config_entry.entry_id
    return link


def remove_missing_devices_and_entities(config_entry, link, device_registry, entity_registry):
    # Ensure every device associated with this config entry still exists
    # otherwise remove the device (and thus entities).
    for device_entry in dr.async_entries_for_config_entry(
        device_registry, config_entry.entry_id
    ):
        for identifier in device_entry.identifiers:
            _LOGGER.debug(
                f"Identifier found in Home Assistant device registry: {identifier[1]} for device: {device_entry.id}"
            )
            if identifier[1] in link.featuresets:
                _LOGGER.debug(f"Identifier exists in Lightwave config for device: {device_entry.id}")
                break
        else:
            _LOGGER.debug(
                f"Identifier does not exist in Lightwave config, removing device: {device_entry.id}"
            )
            device_registry.async_remove_device(device_entry.id)

    # if in debug mode, log the entity registry
    if _LOGGER.isEnabledFor(logging.DEBUG):
        for entity_entry in er.async_entries_for_config_entry(
            entity_registry, config_entry.entry_id
        ):
            _LOGGER.debug(f"Entity registry item: {entity_entry}")
            _LOGGER.debug(f"Entity: {entity_registry.async_get(entity_entry.entity_id)}")
