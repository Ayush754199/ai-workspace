import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './styles.css'

// Ask the PWA worker to check for a new deployment whenever the app opens.
// When a new worker takes control, reload once so mobile browsers do not keep
// rendering an older cached JavaScript bundle. Authentication storage is kept.
if ('serviceWorker' in navigator) {
  let reloadingForUpdate = false
  navigator.serviceWorker.addEventListener('controllerchange', () => {
    if (reloadingForUpdate) return
    reloadingForUpdate = true
    window.location.reload()
  })
  void navigator.serviceWorker.ready.then((registration) => registration.update())
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
