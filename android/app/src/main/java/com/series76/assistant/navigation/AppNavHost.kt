package com.series76.assistant.navigation

import androidx.compose.runtime.Composable
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.toRoute
import com.series76.assistant.data.ConversationRepository
import com.series76.assistant.ui.chat.ChatRoute
import com.series76.assistant.ui.chat.ChatViewModel
import com.series76.assistant.ui.sessions.SessionsRoute
import com.series76.assistant.ui.sessions.SessionsViewModel
import kotlinx.serialization.Serializable

@Serializable
object SessionsDestination

/** [sessionId] null opens a new conversation; [title] is shown in the top bar. */
@Serializable
data class ChatDestination(val sessionId: String? = null, val title: String? = null)

@Composable
fun AppNavHost(repository: ConversationRepository) {
    val navController = rememberNavController()
    NavHost(navController, startDestination = SessionsDestination) {
        composable<SessionsDestination> {
            val vm: SessionsViewModel = viewModel(
                factory = viewModelFactory { initializer { SessionsViewModel(repository) } },
            )
            SessionsRoute(
                viewModel = vm,
                onOpen = { navController.navigate(ChatDestination(it.id, it.title)) },
                onNew = { navController.navigate(ChatDestination()) },
            )
        }
        composable<ChatDestination> { entry ->
            val route = entry.toRoute<ChatDestination>()
            val vm: ChatViewModel = viewModel(
                factory = viewModelFactory { initializer { ChatViewModel(route.sessionId, repository) } },
            )
            ChatRoute(viewModel = vm, title = route.title, onBack = { navController.popBackStack() })
        }
    }
}
