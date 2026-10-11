import "./globals.css";
import Nav from "./Nav";

export const metadata = { title: "askbox" };

export default function RootLayout({ children }) {
  return (
    <html lang="es">
      <body>
        <Nav />
        {children}
      </body>
    </html>
  );
}
