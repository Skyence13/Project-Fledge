import {readFileSync, writeFileSync, mkdirSync, copyFileSync} from "node:fs";

const files = {};
for (const [path, mime] of [["index.html", "text/html"], ["style.css", "text/css"], ["app.js", "text/javascript"]]) {
  files[path] = [mime + "; charset=utf-8", readFileSync("ui/" + path, "utf8")];
}
const snapshot = JSON.parse(readFileSync("initial-summary.json", "utf8").replace(/^\uFEFF/, ""));
const worker = readFileSync("worker.mjs", "utf8");
mkdirSync("dist/server", {recursive:true});
mkdirSync("dist/.openai", {recursive:true});
copyFileSync(".openai/hosting.json", "dist/.openai/hosting.json");
writeFileSync("dist/server/index.js", "const files = " + JSON.stringify(files) + ";\nconst initial = " + JSON.stringify(snapshot) + ";\n" + worker);
