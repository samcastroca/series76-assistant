package com.series76.assistant

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.series76.assistant.ui.auth.AppRoot
import com.series76.assistant.ui.theme.Series76AssistantTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val container = (application as Series76App).container
        setContent {
            Series76AssistantTheme {
                AppRoot(container.sessionManager, container.repository, container.debugRepository)
            }
        }
    }
}
