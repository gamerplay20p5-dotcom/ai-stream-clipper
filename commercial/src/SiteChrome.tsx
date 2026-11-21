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

export function Footer({ t, join, independent, portalUrl = '', manage }: { t: Editorial; join: string; independent: string; portalUrl?: string; manage: string }) {
  const { footer } = t
  return (
    <footer className="site-footer">
      <div className="shell footer-grid">
        <div className="footer-brand">
          <Brand />
          <p>{footer.tagline}</p>
          <a className="button primary" href="#acesso">{join}<ArrowUpRight size={16} aria-hidden="true" /></a>
        </div>
        <nav className="footer-col" aria-labelledby="footer-product">
          <h2 id="footer-product">{footer.product}</h2>
          <Links links={t.links} />
          {portalUrl ? <a href={portalUrl} rel="noopener">{manage}</a> : null}
        </nav>
        <div className="footer-col">
          <h2 id="footer-legal">{footer.legal}</h2>
          <details className="legal-details">
            <summary>{footer.privacy}</summary>
            <p id="privacidade">{footer.pending}</p>
          </details>
          <details className="legal-details">
            <summary>{footer.terms}</summary>
            <p id="termos">{footer.pending}</p>
          </details>
        </div>
      </div>
      <div className="shell footer-end">
        <p>{footer.rights}</p>
        <p>{independent}</p>
      </div>
    </footer>
  )
}
