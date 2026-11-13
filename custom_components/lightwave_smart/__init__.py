import asyncio
import logging

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_API_KEY,
    CONF_LW_AUTH_METHOD,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN_EXPIRY,
    DOMAIN,
    LIGHTWAVE_ENTITIES,
    LIGHTWAVE_LINK2,
    LIGHTWAVE_PLATFORMS,
    SERVICE_RECONNECT,
    SERVICE_RESET_ENABLED_STATUS_TO_DEFAULTS,
    SERVICE_UPDATE,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.const import CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from homeassistant.core import HomeAssistant
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
_LINK_REPAIR_UNSUB = "link_repair_unsub"


def _link_issue_id(entry_id):
    return f"{entry_id}_link_disconnected"


def _clear_link_issue(hass, entry_id):
    ir.async_delete_issue(hass, DOMAIN, _link_issue_id(entry_id))


def _cancel_link_repair_delay(hass, entry_id):
    domain_data = hass.data.get(DOMAIN)
    entry = domain_data.get(entry_id) if isinstance(domain_data, dict) else None
    if not isinstance(entry, dict):
        return
    unsub = entry.pop(_LINK_REPAIR_UNSUB, None)
    if unsub is not None:
        unsub()


def _schedule_link_repair(hass, entry_id):
    """Raise a repair only if this drop is still down after the reconnect window."""
    domain_data = hass.data.get(DOMAIN)
    entry = domain_data.get(entry_id) if isinstance(domain_data, dict) else None
    if not isinstance(entry, dict) or entry.get(_LINK_REPAIR_UNSUB) is not None:
        return

    def _raise_if_still_down(_now):
        current_domain = hass.data.get(DOMAIN)
        current = current_domain.get(entry_id) if isinstance(current_domain, dict) else None
        if not isinstance(current, dict):
            return
        current.pop(_LINK_REPAIR_UNSUB, None)
        link = current.get(LIGHTWAVE_LINK2)
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

    entry[_LINK_REPAIR_UNSUB] = async_call_later(
        hass, _LINK_REPAIR_DELAY, _raise_if_still_down
    )


def _iter_loaded_entries(hass: HomeAssistant):
    """Yield (entry_id, entry_data) for loaded Lightwave config entries."""
    domain_data = hass.data.get(DOMAIN)
    if not isinstance(domain_data, dict):
        return
    for entry_id, entry_data in list(domain_data.items()):
        if isinstance(entry_data, dict) and LIGHTWAVE_LINK2 in entry_data:
            yield entry_id, entry_data


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

    async def service_handle_reconnect(call):
        _LOGGER.debug("Received service call reconnect")
        for _entry_id, entry_data in _iter_loaded_entries(hass):
            link = entry_data[LIGHTWAVE_LINK2]
            try:
                await link.async_deactivate(source="service_handle_reconnect")
                await link.async_activate(
                    source="service_handle_reconnect",
                    connect_callback=link.async_get_hierarchy,
                )
            except Exception as e:
                _LOGGER.error("Error reconnecting Lightwave link: %s", e)

    async def service_handle_update_states(call):
        _LOGGER.debug("Received service call update states")
        for _entry_id, entry_data in _iter_loaded_entries(hass):
            link = entry_data[LIGHTWAVE_LINK2]
            await link.async_update_featureset_states()
            for ent in entry_data.get(LIGHTWAVE_ENTITIES, ()):
                if ent.hass is not None:
                    ent.async_schedule_update_ha_state(True)

    async def service_handle_reset_enabled_status_to_defaults(call):
        """Reset enabled status to defaults."""
        _LOGGER.debug("reset_enabled_status_to_defaults: Received service call reset enabled status to defaults")
        entity_registry = er.async_get(hass)

        count = 0
        enabled_count = 0
        for entry_id, entry_data in _iter_loaded_entries(hass):
            config_entry = hass.config_entries.async_get_entry(entry_id)
            if config_entry:
                entities = entry_data.get(LIGHTWAVE_ENTITIES, ())
                for entity_entry in er.async_entries_for_config_entry(entity_registry, entry_id):
                    count += 1
                    default_disabled = False

                    entity_object = None
                    for ent in entities:
                        if hasattr(ent, "unique_id") and ent.unique_id == entity_entry.unique_id:
                            entity_object = ent
                            break

                    if entity_object is None:
                        _LOGGER.warning(
                            f"reset_enabled_status_to_defaults: Could not find entity object for {entity_entry.entity_id}/{entity_entry.unique_id}"
                        )
                        continue

                    default_disabled = not entity_object.entity_description.entity_registry_enabled_default
                    if entity_entry.disabled == default_disabled:
                        continue

                    _LOGGER.info(
                        f"reset_enabled_status_to_defaults: Entity {entity_entry.entity_id}/{entity_entry.unique_id} will be changed to disabled: {default_disabled}"
                    )

                    entity_registry.async_update_entity(
                        entity_entry.entity_id,
                        disabled_by=er.RegistryEntryDisabler.INTEGRATION if default_disabled else None,
                    )
                    enabled_count += 1

        _LOGGER.info(
            f"reset_enabled_status_to_defaults: Entities have been reset to defaults: {enabled_count} of {count}"
        )

    hass.services.async_register(DOMAIN, SERVICE_RECONNECT, service_handle_reconnect)
    hass.services.async_register(DOMAIN, SERVICE_UPDATE, service_handle_update_states)
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESET_ENABLED_STATUS_TO_DEFAULTS,
        service_handle_reset_enabled_status_to_defaults,
    )

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    entry_id = config_entry.entry_id
    auth_method = config_entry.data.get(CONF_LW_AUTH_METHOD) or "password"

    link = await setup_link_lw(hass, config_entry)
    had_access_token = (
        auth_method != "oauth"
        and bool(link.auth.get_tokens().get("access_token"))
    )

    def _on_link_disconnect():
        _schedule_link_repair(hass, entry_id)

    def _on_link_connect():
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)

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

    entry_data = {
        LIGHTWAVE_LINK2: link,
        LIGHTWAVE_ENTITIES: [],
        LIGHTWAVE_PLATFORMS: [],
    }
    hass.data[DOMAIN][entry_id] = entry_data

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
        entry_data[LIGHTWAVE_PLATFORMS].extend(all_platforms)
    except BaseException:
        await _safe_deactivate_link(link, "setup_platform_failed")
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        domain_data = hass.data.get(DOMAIN)
        if isinstance(domain_data, dict):
            domain_data.pop(entry_id, None)
        raise

    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: ConfigEntry, source: str = "unload"
) -> bool:
    """Unload a config entry."""
    entry_id = config_entry.entry_id
    _LOGGER.info("Unloading config entry: '%s' from %s", entry_id, source)

    domain_data = hass.data.get(DOMAIN)
    entry_data = domain_data.get(entry_id) if isinstance(domain_data, dict) else None
    if not isinstance(entry_data, dict):
        _LOGGER.warning("Config entry '%s' was never loaded, skipping unload", entry_id)
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        if isinstance(domain_data, dict):
            domain_data.pop(entry_id, None)
        return True

    loaded_platforms = list(entry_data.get(LIGHTWAVE_PLATFORMS, ()))
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
        link = entry_data.get(LIGHTWAVE_LINK2)
        if link is not None:
            await _safe_deactivate_link(link, "async_unload_entry")

        # A drop while platforms unload can start a repair. Cancel that wait and
        # delete any repair once the link has been stopped.
        _cancel_link_repair_delay(hass, entry_id)
        _clear_link_issue(hass, entry_id)
        if isinstance(domain_data, dict):
            domain_data.pop(entry_id, None)

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
