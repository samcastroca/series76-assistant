package com.series76.assistant.data

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities

/** Tells "no network on the device" apart from "the backend is down" after an I/O failure. */
fun interface ConnectivityChecker {
    fun isOnline(): Boolean
}

class AndroidConnectivityChecker(context: Context) : ConnectivityChecker {
    private val manager = context.getSystemService(ConnectivityManager::class.java)

    // INTERNET, not VALIDATED: a backend on the local network needs no internet access.
    override fun isOnline(): Boolean {
        val capabilities = manager.getNetworkCapabilities(manager.activeNetwork) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }
}
