package com.series76.assistant.data.api.model

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// A product card, built by the backend from tool results. A null spec means "not published";
// the UI never shows it as 0.

@Serializable
data class Product(
    @SerialName("part_number") val partNumber: String,
    @SerialName("series_size") val seriesSize: Int,
    val variant: String,
    val enclosure: Enclosure,
    @SerialName("power_supply") val powerSupply: PowerSupply,
    val certifications: List<String>,
    @SerialName("csa_certified") val csaCertified: Boolean,
    @SerialName("hazardous_locations") val hazardousLocations: List<String>?,
    @SerialName("data_available") val dataAvailable: Boolean,
    val service: List<ProductService>,
    val electrical: List<ProductElectrical>,
    @SerialName("motor_power_w") val motorPowerW: Int?,
    val notes: List<String> = emptyList(),
    val source: Source,
    @SerialName("matched_service") val matchedService: ServiceType? = null,
    @SerialName("meets_requirements") val meetsRequirements: Boolean = true,
    val unmet: List<FilterName> = emptyList(),
)

@Serializable
data class PowerSupply(
    @SerialName("voltage_v") val voltageV: Int,
    val phase: Int?,
    @SerialName("current_type") val currentType: CurrentType,
)

@Serializable
data class Torque(
    @SerialName("in_lb") val inLb: Int?,
    val nm: Int?,
)

@Serializable
data class ProductService(
    val type: ServiceType,
    val torque: Torque,
    @SerialName("duty_cycle_pct") val dutyCyclePct: Int?,
    @SerialName("cycles_per_hour") val cyclesPerHour: Int?,
    @SerialName("starts_per_hour") val startsPerHour: Int?,
)

@Serializable
data class ProductElectrical(
    @SerialName("frequency_hz") val frequencyHz: Int?, // 50 or 60; null on 24 V (one value)
    @SerialName("speed_s") val speedS: Int?,
    @SerialName("fla_a") val flaA: Double?,
    @SerialName("lra_a") val lraA: Double?,
)

@Serializable
data class Source(
    val table: String,
    val page: Int,
)
