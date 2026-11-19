/* Regression check for the 2026-11-19 Instagram bug: the UI must never show a privacy different from
 * the value that will be sent. Run: npm run check:privacy (esbuild + node, no extra dependency). */
import { renderToStaticMarkup } from "react-dom/server";
import PrivacyPicker, { initialPrivacy, publicOnly } from "../src/components/PrivacyPicker";

let failed = 0;
function ok(cond: boolean, msg: string) {
  console.log(`${cond ? "ok  " : "FAIL"} ${msg}`);
  if (!cond) failed++;
}
const html = (options: string[], value: string, platform?: string) =>
  renderToStaticMarkup(<PrivacyPicker options={options} value={value} onChange={() => {}} platform={platform} />);
const selected = (h: string) => (h.match(/<option value="([^"]*)"[^>]*selected/) ?? [])[1];

// the old bug: Instagram account, state "private"
ok(initialPrivacy(["public"]) === "", "Instagram nunca começa com 'private' nem com 'public' silencioso");
const ig = html(["public"], "private", "instagram");
ok(!ig.includes("<select") && ig.includes('type="checkbox"') && !ig.includes("checked"),
  "Instagram com estado inválido mostra a confirmação DESMARCADA (nada de 'Público' aparente)");
ok(html(["public"], "public", "instagram").includes("checked"), "confirmado -> checkbox marcado e valor 'public'");
ok(publicOnly(["public"]) && !publicOnly(["private", "public"]), "detecção de conta só-pública");
// YouTube
ok(initialPrivacy(["private", "unlisted", "public"]) === "private", "YouTube começa privado");
ok(selected(html(["private", "unlisted", "public"], "private")) === "private", "select mostra exatamente 'private'");
ok(selected(html(["private", "unlisted", "public"], "bogus")) === "", "valor inválido mostra o placeholder, não outra opção");
ok(selected(html(["private", "unlisted", "public"], "")) === "", "vazio mostra o placeholder");
// TikTok: choice is mandatory
ok(initialPrivacy(["SELF_ONLY", "PUBLIC_TO_EVERYONE"]) === "", "TikTok exige escolha");
ok(initialPrivacy(["private", "public"], "public") === "public", "valor atual válido é mantido");

if (failed) {
  console.error(`${failed} verificação(ões) falharam`);
  process.exit(1);
}
console.log("todas as verificações de privacidade passaram");
