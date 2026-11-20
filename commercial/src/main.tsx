import { createRoot, hydrateRoot } from 'react-dom/client'
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/instrument-sans'
import { App } from './App'
import { browserPreferences } from './market'
import './styles.css'

const root = document.getElementById('root')!
const { language, market } = root.dataset
if ((language === 'pt-BR' || language === 'en') && (market === 'BR' || market === 'US')) {
  hydrateRoot(root, <App initial={{ language, market }} />)
} else {
  createRoot(root).render(<App initial={browserPreferences()} />)
}
