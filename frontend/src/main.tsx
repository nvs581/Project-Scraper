import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

/**
 * Application Entry Point
 * 
 * Initializes the React 18+ root and renders the main App component.
 * Configures the environment with StrictMode and global styles.
 */
createRoot(document.getElementById('root')!).render(
  /**
   * StrictMode helps identify side effects and deprecated patterns during development.
   * It is automatically omitted in production builds.
   */
  <StrictMode>
    <App />
  </StrictMode>,
)
