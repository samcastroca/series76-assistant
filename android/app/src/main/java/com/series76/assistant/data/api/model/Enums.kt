package com.series76.assistant.data.api.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// Closed sets of the API contract (backend/app/schemas/conversation.py and catalog.py).

@Serializable
enum class ResponseType {
    @SerialName("detail") DETAIL,
    @SerialName("recommendation") RECOMMENDATION,
    @SerialName("comparison") COMPARISON,
    @SerialName("clarification") CLARIFICATION,
    @SerialName("no_match") NO_MATCH,
    @SerialName("not_found") NOT_FOUND,
    @SerialName("no_data") NO_DATA,
    @SerialName("error") ERROR,
}

@Serializable
enum class HighlightField {
    @SerialName("torque") TORQUE,
    @SerialName("speed_s") SPEED_S,
    @SerialName("duty_cycle_pct") DUTY_CYCLE_PCT,
    @SerialName("power_supply") POWER_SUPPLY,
    @SerialName("certifications") CERTIFICATIONS,
    @SerialName("enclosure") ENCLOSURE,
    @SerialName("series_size") SERIES_SIZE,
    @SerialName("service") SERVICE,
}

@Serializable
enum class TurnWarning {
    @SerialName("tolerance_applied") TOLERANCE_APPLIED,
    @SerialName("service_type_unspecified") SERVICE_TYPE_UNSPECIFIED,
    @SerialName("not_csa_certified") NOT_CSA_CERTIFIED,
    @SerialName("no_published_data") NO_PUBLISHED_DATA,
    @SerialName("multiple_filters_blocking") MULTIPLE_FILTERS_BLOCKING,
    @SerialName("numbers_removed") NUMBERS_REMOVED,
    @SerialName("tool_error") TOOL_ERROR,
}

@Serializable
enum class Enclosure {
    @SerialName("weatherproof") WEATHERPROOF,
    @SerialName("explosionproof") EXPLOSIONPROOF,
}

@Serializable
enum class CurrentType {
    @SerialName("AC") AC,
    @SerialName("DC") DC,
    @SerialName("AC/DC") AC_DC,
}

@Serializable
enum class ServiceType {
    @SerialName("on_off") ON_OFF,
    @SerialName("modulating") MODULATING,
    @SerialName("unspecified") UNSPECIFIED,
}

@Serializable
enum class FilterName {
    @SerialName("min_torque") MIN_TORQUE,
    @SerialName("max_speed_s") MAX_SPEED_S,
    @SerialName("min_duty_cycle_pct") MIN_DUTY_CYCLE_PCT,
    @SerialName("series_size") SERIES_SIZE,
    @SerialName("service_type") SERVICE_TYPE,
    @SerialName("current_type") CURRENT_TYPE,
    @SerialName("phase") PHASE,
    @SerialName("voltage_v") VOLTAGE_V,
    @SerialName("csa_required") CSA_REQUIRED,
    @SerialName("enclosure") ENCLOSURE,
}

@Serializable
enum class Role {
    @SerialName("user") USER,
    @SerialName("assistant") ASSISTANT,
}

@Serializable
enum class MessageStatus {
    @SerialName("ok") OK,
    @SerialName("error") ERROR,
}
