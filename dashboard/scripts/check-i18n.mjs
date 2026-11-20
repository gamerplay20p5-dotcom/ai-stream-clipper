/* Translation check. Run: npm run check:i18n
 * 1. every t("...") text in src/ has an English entry in src/i18n/en.ts (fails otherwise);
 * 2. the English entry keeps the same {placeholders} (fails otherwise);
 * 3. lists Portuguese-looking text still outside t() (warning; --strict makes it fail).
 * Uses the TypeScript compiler already in devDependencies (no extra dependency). */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const src = path.join(root, "src");
const strict = process.argv.includes("--strict");
const dump = process.argv.includes("--dump-missing");
const ACC = /[ãõçáéíóúâêôàÃÕÇÁÉÍÓÚÂÊÔÀ]/;
const PT = /(^|[\s(])(de|do|da|dos|das|para|pra|com|sem|nos|nas|um|uma|ou|os|as|ao|em|por|que|Motivo|motivo|cortes?|Corte|Ver|Abrir|Salvar|Nenhum|nenhum|Todos|todos|agora|ainda|Sim|sim|foi|ponto|alto|seu|sua|mais|como|quando|pelo|pela|isso|este|esta|aqui|tem|ter|vai|vez|depois|antes|sobre|onde|qual)(?=[\s:.,)!?]|$)/;
const isClass = (n) => { for (let p = n.parent; p; p = p.parent) { if (ts.isJsxAttribute(p)) return /^(className|style|key|to|href|value|type|id)$/.test(p.name.getText()); if (ts.isFunctionLike(p)) return false; } return false; };
const looksPt = (s) => ACC.test(s) || PT.test(s);

function files(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = path.join(dir, d.name);
    if (d.isDirectory()) return d.name === "i18n" ? [] : files(p);
    return /\.tsx?$/.test(d.name) && !d.name.endsWith(".d.ts") && d.name !== "i18n.ts" ? [p] : [];
  });
}

function parse(file) {
  return ts.createSourceFile(file, fs.readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true,
    file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
}

// English dictionary: object literal in src/i18n/en.ts
const enFile = parse(path.join(src, "i18n", "en.ts"));
const en = new Map();
enFile.forEachChild(function walk(n) {
  if (ts.isPropertyAssignment(n) && (ts.isStringLiteral(n.name) || ts.isIdentifier(n.name)) &&
      (ts.isStringLiteral(n.initializer) || ts.isNoSubstitutionTemplateLiteral(n.initializer))) {
    en.set(n.name.text, n.initializer.text);
  }
  n.forEachChild(walk);
});

const keys = new Map(); // text -> first location
const leftovers = [];
for (const file of files(src)) {
  const sf = parse(file);
  const rel = path.relative(root, file).replace(/\\/g, "/");
  const where = (n) => `${rel}:${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1}`;
  const insideT = (n) => {
    for (let c = n, p = n.parent; p; c = p, p = p.parent) {
      // only the message (first argument) counts; values passed in {vars} still need their own t()
      if (ts.isCallExpression(p) && ts.isIdentifier(p.expression) && p.expression.text === "t") return p.arguments[0] === c;
      if (ts.isImportDeclaration(p)) return true;
    }
    return false;
  };
  sf.forEachChild(function walk(n) {
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === "t" && n.arguments[0]) {
      const a = n.arguments[0];
      const lit = (x) => ts.isStringLiteral(x) || ts.isNoSubstitutionTemplateLiteral(x);
      const opts = lit(a) ? [a] : ts.isConditionalExpression(a) && lit(a.whenTrue) && lit(a.whenFalse) ? [a.whenTrue, a.whenFalse] : null;
      if (opts) {
        for (const o of opts) if (!keys.has(o.text)) keys.set(o.text, where(o));
      } else {
        leftovers.push(`${where(a)} t() com texto dinâmico (não traduzível): ${a.getText().slice(0, 60)}`);
      }
    }
    if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isJsxText(n) || ts.isTemplateHead(n) ||
         ts.isTemplateMiddle(n) || ts.isTemplateTail(n)) && looksPt(n.text ?? n.getText()) && !insideT(n) && !isClass(n)) {
      if (!ts.isLiteralTypeNode(n.parent)) leftovers.push(`${where(n)} ${(n.text ?? n.getText()).trim().slice(0, 80)}`);
    } else if ((ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) && /[A-Za-zÀ-ú]/.test(n.text) && !insideT(n)) {
      // a sibling already translated -> this text is interface text too (label maps, a ? t("x") : "y")
      const isTCall = (x) => x && ts.isCallExpression(x) && ts.isIdentifier(x.expression) && x.expression.text === "t";
      const p = n.parent;
      const words = /\s|[À-ú]/.test(n.text); // ids, routes and css classes have neither
      const sibT = words && ts.isPropertyAssignment(p) && p.initializer === n && ts.isObjectLiteralExpression(p.parent) &&
          p.parent.properties.some((q) => ts.isPropertyAssignment(q) && isTCall(q.initializer)) ||
        ts.isConditionalExpression(p) && p.condition !== n && !/^[\/#.]|-/.test(n.text) &&
          (isTCall(p.whenTrue) || isTCall(p.whenFalse));
      if (sibT) leftovers.push(`${where(n)} ${n.text.slice(0, 80)} (vizinho já traduzido)`);
    }
    n.forEachChild(walk);
  });
}

const vars = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");
const missing = [...keys].filter(([k]) => !en.has(k));
const badVars = [...keys].filter(([k]) => en.has(k) && vars(k) !== vars(en.get(k)));
const unused = [...en.keys()].filter((k) => !keys.has(k));

if (dump) {
  console.log(JSON.stringify(missing.map(([k]) => k), null, 1));
  process.exit(0);
}
for (const [k, at] of missing) console.log(`FALTA ${at} ${JSON.stringify(k)}`);
for (const [k, at] of badVars) console.log(`PLACEHOLDER ${at} ${JSON.stringify(k)} -> ${JSON.stringify(en.get(k))}`);
for (const l of leftovers) console.log(`AVISO ${l}`);
console.log(`${keys.size} textos, ${en.size} traduções, ${missing.length} faltando, ${badVars.length} placeholders errados, ` +
  `${unused.length} traduções sem uso, ${leftovers.length} avisos`);
if (missing.length || badVars.length || (strict && leftovers.length)) process.exit(1);
