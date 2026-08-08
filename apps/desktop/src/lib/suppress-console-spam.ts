// Suppress @assistant-ui/store's "useClientLookup: Clamped stale index" spam.
// The library logs this on every store replacement race (session switch,
// streaming, deletion) — hundreds of times per action. It's transient and
// self-healing, but the volume masks real errors in the console.
// Upstream-tracked: assistant-ui/assistant-ui#4051, #3652.
const _originalLog = console.log
const _SPAM_PATTERNS = [/^useClientLookup: Clamped stale index/]

console.log = (...args: unknown[]) => {
  if (args.length > 0 && typeof args[0] === 'string') {
    for (const pattern of _SPAM_PATTERNS) {
      if (pattern.test(args[0])) return
    }
  }
  _originalLog.apply(console, args as [unknown, ...unknown[]])
}
