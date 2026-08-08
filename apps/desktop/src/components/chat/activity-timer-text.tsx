import { cn } from '@/lib/utils'

import { formatElapsed } from './activity-timer'
import { StableText } from './stable-text'

interface ActivityTimerTextProps {
  seconds: number
  /** When set, display "elapsed/max" format instead of just "elapsed". */
  maxSeconds?: number
  className?: string
}

export function ActivityTimerText({ seconds, maxSeconds, className }: ActivityTimerTextProps) {
  const text = maxSeconds != null && maxSeconds > 0
    ? `${formatElapsed(seconds)}/${formatElapsed(maxSeconds)}`
    : formatElapsed(seconds)

  return (
    <StableText
      className={cn(
        // Tinted with --dt-midground (very low alpha) so the timer reads
        // as part of the same "live signal" cluster as the dither block /
        // arc-border / working-session dot, instead of being neutral chrome.
        'shrink-0 text-[0.56rem] leading-none tracking-[0.02em] text-midground/55',
        maxSeconds != null && seconds >= maxSeconds && 'text-amber/70',
        className
      )}
    >
      {text}
    </StableText>
  )
}
