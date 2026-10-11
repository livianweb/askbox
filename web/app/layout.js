import "./globals.css";
import Nav from "./Nav";

export const metadata = {
  title: "askbox — Pasarela unificada LLM",
  description: "Pasarela y proxy inteligente de modelos LLM con enrutamiento por tiers, keypool cifrado y caché.",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body>
        <div className="app-wrapper">
          <Nav />
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
