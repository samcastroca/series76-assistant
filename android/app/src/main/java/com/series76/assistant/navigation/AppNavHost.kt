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
import com.series76.assistant.data.DebugRepository
import com.series76.assistant.data.api.model.UserOut
import com.series76.assistant.ui.chat.ChatRoute
import com.series76.assistant.ui.chat.ChatViewModel
import com.series76.assistant.ui.debug.DebugRoute
import com.series76.assistant.ui.debug.DebugViewModel
import com.series76.assistant.ui.sessions.SessionsRoute
import com.series76.assistant.ui.sessions.SessionsViewModel
import kotlinx.serialization.Serializable

@Serializable
object SessionsDestination

/** [sessionId] null opens a new conversation; [title] is shown in the top bar. */
@Serializable
data class ChatDestination(val sessionId: String? = null, val title: String? = null)

/** The traces of a conversation; [focusMessageId] is the answer whose bug icon was tapped. */
@Serializable
data class DebugDestination(val sessionId: String, val focusMessageId: String? = null)

/**
 * How the chat opens Debug, or null when [user] may not: then no Debug entry is composed and the
 * Debug route is not registered.
 */
fun debugOpener(user: UserOut, navigate: (DebugDestination) -> Unit): ((String, String?) -> Unit)? =
    if (user.isDeveloper) { sessionId, messageId -> navigate(DebugDestination(sessionId, messageId)) } else null

/** The signed-in graph. Disposed when the session ends, so its back stack goes with it. */
@Composable
fun AppNavHost(
    repository: ConversationRepository,
    debugRepository: DebugRepository,
    user: UserOut,
    onSignOut: () -> Unit,
) {
    val navController = rememberNavController()
    NavHost(navController, startDestination = SessionsDestination) {
        composable<SessionsDestination> {
            val vm: SessionsViewModel = viewModel(
                factory = viewModelFactory { initializer { SessionsViewModel(repository) } },
            )
            SessionsRoute(
                viewModel = vm,
                user = user,
                onSignOut = onSignOut,
                onOpen = { navController.navigate(ChatDestination(it.id, it.title)) },
                onNew = { navController.navigate(ChatDestination()) },
            )
        }
        composable<ChatDestination> { entry ->
            val route = entry.toRoute<ChatDestination>()
            val vm: ChatViewModel = viewModel(
                factory = viewModelFactory { initializer { ChatViewModel(route.sessionId, repository) } },
            )
            ChatRoute(
                viewModel = vm,
                title = route.title,
                onBack = { navController.popBackStack() },
                // Single top: a double tap does not stack two Debug screens.
                onOpenDebug = debugOpener(user) { navController.navigate(it) { launchSingleTop = true } },
            )
        }
        if (user.isDeveloper) { // the route does not even exist for engineers
            composable<DebugDestination> { entry ->
                val route = entry.toRoute<DebugDestination>()
                val vm: DebugViewModel = viewModel(
                    factory = viewModelFactory {
                        initializer { DebugViewModel(route.sessionId, route.focusMessageId, repository, debugRepository) }
                    },
                )
                DebugRoute(viewModel = vm, onBack = { navController.popBackStack() })
            }
        }
    }
}
