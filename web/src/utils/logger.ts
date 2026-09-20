const canLogDebug = import.meta.env.DEV

export const logger = {
  debug: (...args: unknown[]) => {
    if (canLogDebug) {
      globalThis.console?.debug?.(...args)
    }
  },
}
