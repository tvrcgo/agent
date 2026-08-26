import React from "react"
import ReactDOM from "react-dom/client"
import { ThemeProvider } from "@/components/theme-provider"
import { ToasterProvider } from "@/components/ui/sonner"
import { DesktopShell } from "@/components/desktop-shell"
import "./globals.css"
import "@/components/chat/tokens.css"
import "@/components/chat/chat.css"

function App() {
  return (
    <ThemeProvider>
      <DesktopShell />
      <ToasterProvider />
    </ThemeProvider>
  )
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
