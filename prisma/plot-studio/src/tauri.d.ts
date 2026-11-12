interface TauriCore {
  invoke<T>(
    command: string,
    args?: Record<string, unknown>,
  ): Promise<T>
}

interface TauriGlobal {
  core?: TauriCore
}

interface Window {
  __TAURI__?: TauriGlobal
}
