/** Logo askbox (cubo estrellado) desde /public/logo.webp */
export default function BrandLogo({ size = 32, className = "" }) {
  const cls = ["brand-logo-img", className].filter(Boolean).join(" ");
  return (
    <img
      src="/logo.webp"
      alt=""
      width={size}
      height={size}
      className={cls}
      decoding="async"
    />
  );
}
