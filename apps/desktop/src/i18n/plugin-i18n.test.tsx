import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { I18nProvider, useI18n } from './context'
import { createPluginI18n, registerPluginLocales, translatePlugin, usePluginI18n } from './plugin-i18n'
import { setRuntimeI18nLocale } from './runtime'

const noopTrack = (dispose: () => void) => dispose

afterEach(() => {
  cleanup()
  setRuntimeI18nLocale('en')
})

describe('plugin locale registry', () => {
  it('resolves the active locale, falling back to English then the raw key', () => {
    const dispose = registerPluginLocales('cost', {
      en: { panel: { title: 'Cost' }, spent: (n: number) => `$${n} spent` },
      ru: { panel: { title: 'Стоимость' } }
    })

    expect(translatePlugin('cost', 'ru', 'panel.title', [])).toBe('Стоимость')
    // Missing in ru → English.
    expect(translatePlugin('cost', 'ru', 'spent', [7])).toBe('$7 spent')
    // Missing everywhere → the key itself.
    expect(translatePlugin('cost', 'ru', 'nope', [])).toBe('nope')

    dispose()
  })

  it('scopes bundles per plugin — no cross-read', () => {
    const a = registerPluginLocales('a', { en: { hi: 'from a' } })
    const b = registerPluginLocales('b', { en: { hi: 'from b' } })

    expect(translatePlugin('a', 'en', 'hi', [])).toBe('from a')
    expect(translatePlugin('b', 'en', 'hi', [])).toBe('from b')
    // An unknown plugin resolves to the key.
    expect(translatePlugin('c', 'en', 'hi', [])).toBe('hi')

    a()
    b()
  })

  it('merges repeated registrations and drops everything on dispose', () => {
    const one = registerPluginLocales('merge', { en: { a: 'A' } })
    const two = registerPluginLocales('merge', { en: { b: 'B' }, ru: { a: 'А' } })

    expect(translatePlugin('merge', 'en', 'a', [])).toBe('A')
    expect(translatePlugin('merge', 'en', 'b', [])).toBe('B')
    expect(translatePlugin('merge', 'ru', 'a', [])).toBe('А')

    one()
    two()

    expect(translatePlugin('merge', 'en', 'a', [])).toBe('a')
  })

  it('ctx.i18n.t reads the app runtime locale', () => {
    const i18n = createPluginI18n('runtime-plugin', noopTrack)
    i18n.register({ en: { greet: 'hello' }, ru: { greet: 'привет' } })

    expect(i18n.t('greet')).toBe('hello')

    setRuntimeI18nLocale('ru')
    expect(i18n.t('greet')).toBe('привет')
  })
})

function Probe({ pluginId }: { pluginId: string }) {
  const t = usePluginI18n(pluginId)

  return <p data-testid="copy">{t('greet')}</p>
}

function SwitchToRu() {
  const { setLocale } = useI18n()

  return (
    <button onClick={() => void setLocale('ru')} type="button">
      to ru
    </button>
  )
}

describe('usePluginI18n', () => {
  it('re-renders on a locale switch', () => {
    const dispose = registerPluginLocales('hooked', {
      en: { greet: 'hello' },
      ru: { greet: 'привет' }
    })

    render(
      <I18nProvider configClient={null}>
        <SwitchToRu />
        <Probe pluginId="hooked" />
      </I18nProvider>
    )

    expect(screen.getByTestId('copy').textContent).toBe('hello')

    fireEvent.click(screen.getByRole('button'))

    expect(screen.getByTestId('copy').textContent).toBe('привет')

    dispose()
  })

  it('picks up a bundle registered after mount', () => {
    render(
      <I18nProvider configClient={null}>
        <Probe pluginId="late" />
      </I18nProvider>
    )

    expect(screen.getByTestId('copy').textContent).toBe('greet')

    let dispose = () => {}
    act(() => {
      dispose = registerPluginLocales('late', { en: { greet: 'landed' } })
    })

    expect(screen.getByTestId('copy').textContent).toBe('landed')

    dispose()
  })
})
