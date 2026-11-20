import { t } from "../i18n";
export function Brand() {
  return (
    <span className="product-brand">
      <img src="/site/clipper-mark.svg" alt="" width={40} height={40} />
      <span>
        <strong>{t("AI Stream Clipper")}</strong>
        <span>{t("by HTS / HowTo.Software")}</span>
      </span>
    </span>
  );
}
