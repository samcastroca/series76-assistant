package com.series76.assistant.data.api

import kotlinx.serialization.json.Json

/**
 * The app's JSON configuration. Unknown keys are ignored so an additive backend field does not
 * break an installed app; the contract tests decode the fixtures strictly instead.
 */
val ApiJson = Json { ignoreUnknownKeys = true }
