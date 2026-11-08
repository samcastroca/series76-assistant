package com.series76.assistant.ui.auth

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner

/**
 * One [ViewModelStore] per part of the UI that belongs to a session state ("signed out", "signed
 * in as X"). Held by the activity, so a configuration change keeps them; [keepOnly] clears every
 * other part, so leaving a state really ends its ViewModels: after a sign-out the previous user's
 * conversations, chat and navigation stack are released, and the login form starts empty.
 */
class BranchViewModelStores : ViewModel() {
    private val owners = mutableMapOf<String, ViewModelStoreOwner>()

    fun owner(branch: String): ViewModelStoreOwner =
        owners.getOrPut(branch) {
            val store = ViewModelStore()
            object : ViewModelStoreOwner {
                override val viewModelStore = store
            }
        }

    fun keepOnly(branch: String) {
        owners.keys.filter { it != branch }.forEach { owners.remove(it)?.viewModelStore?.clear() }
    }

    override fun onCleared() {
        owners.values.forEach { it.viewModelStore.clear() }
        owners.clear()
    }
}
