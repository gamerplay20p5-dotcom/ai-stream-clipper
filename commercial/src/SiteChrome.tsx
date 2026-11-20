import { useRef, type KeyboardEvent } from 'react'
import { ArrowUpRight, Menu, X } from 'lucide-react'
import type { Language } from './content'
import type { Editorial } from './editorial'

function Brand() {
  return <a className="brand" href="#top" translate="no"><img src="/brand.svg" width="36" height="36" alt="" /><span>AI Stream Clipper <small>by HTS</small></span></a>
}

function Links({ links, onNavigate }: { links: Editorial['links']; onNavigate?: (id: string) => void }) {
  return <>{Object.entries(links).map(([id, label]) => <a key={id} href={`#${id}`} onClick={() => onNavigate?.(id)}>{label}</a>)}</>
}

interface HeaderProps {
  t: Editorial
  language: Language
  languageLabel: string
  onLanguage: (language: Language) => void
  join: string
}

export function Header({ t, language, languageLabel, onLanguage, join }: HeaderProps) {
  const menu = useRef<HTMLDialogElement>(null)
  const close = useRef<HTMLButtonElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)

  function navigate(id: string) {
    menu.current?.close()
    document.getElementById(id)?.focus({ preventScroll: true })
  }

  function dismiss() {
    menu.current?.close()
    trigger.current?.focus()
  }

  function cycleFocus(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.key !== 'Tab') return
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button, a[href]'))
    const index = items.indexOf(document.activeElement as HTMLElement)
    const next = event.shiftKey ? (index <= 0 ? items.length - 1 : index - 1) : (index + 1) % items.length
    event.preventDefault()
    items[next]?.focus()
  }

  return (
    <header className="site-header" id="top">
      <div className="header-inner shell">
        <Brand />
        <nav className="desktop-nav" aria-label={t.navigation}><Links links={t.links} /></nav>
        <div className="header-actions">
          <div className="segmented" role="group" aria-label={languageLabel}>
            <button lang="pt-BR" type="button" aria-pressed={language === 'pt-BR'} onClick={() => onLanguage('pt-BR')}>PT-BR</button>
            <button lang="en" type="button" aria-pressed={language === 'en'} onClick={() => onLanguage('en')}>EN</button>
          </div>
          <a className="button header-join" href="#acesso">{join}<ArrowUpRight size={15} aria-hidden="true" /></a>
          <button ref={trigger} type="button" className="menu-trigger" aria-label={t.openMenu} aria-controls="mobile-menu" aria-haspopup="dialog" onClick={() => { menu.current?.showModal(); close.current?.focus() }}><Menu size={22} aria-hidden="true" /></button>
        </div>
      </div>
      <dialog ref={menu} id="mobile-menu" className="mobile-menu" aria-labelledby="menu-title" onKeyDown={cycleFocus} onCancel={event => { event.preventDefault(); dismiss() }}>
        <div className="mobile-menu-head"><h2 id="menu-title">{t.navigation}</h2><button ref={close} type="button" aria-label={t.closeMenu} onClick={dismiss}><X size={23} aria-hidden="true" /></button></div>
        <nav aria-label={t.navigation}><Links links={t.links} onNavigate={navigate} /><a href="#acesso" onClick={() => navigate('acesso')}>{join}</a></nav>
      </dialog>
    </header>
  )
}

export function Footer({ t, join, independent }: { t: Editorial; join: string; independent: string }) {
  return (
    <footer className="site-footer">
      <div className="shell">
        <div className="footer-main">
          <Brand />
          <nav aria-label={t.navigation}><Links links={t.links} /><a href="#acesso">{join}</a></nav>
        </div>
        <div className="footer-end">
          <p>{independent}</p>
          <details className="legal-details">
            <summary>{t.legal.label}</summary>
            <div>
              <a href="#privacidade">{t.legal.privacy}</a><a href="#termos">{t.legal.terms}</a>
              <section id="privacidade"><h2>{t.legal.privacy}</h2><p>{t.legal.pending}</p></section>
              <section id="termos"><h2>{t.legal.terms}</h2><p>{t.legal.pending}</p></section>
            </div>
          </details>
        </div>
      </div>
    </footer>
  )
}
