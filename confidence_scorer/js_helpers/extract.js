#!/usr/bin/env node
const { parse } = require("@babel/parser");
const generatorPkg = require("@babel/generator");
const generate = generatorPkg.default || generatorPkg;
const babelCore = require("@babel/core");

function stripTypes(src) {
  try {
    const out = babelCore.transformSync(src, {
      presets: [["@babel/preset-typescript", { allExtensions: true, isTSX: false }]],
      filename: "snippet.ts",
      babelrc: false,
      configFile: false,
      compact: false,
    });
    return out && out.code ? out.code : src;
  } catch (e) {
    return src;
  }
}

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => (data += chunk));
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function typeToString(tsTypeAnnotation) {
  if (!tsTypeAnnotation) return null;
  try {
    return generate(tsTypeAnnotation).code;
  } catch (e) {
    return null;
  }
}

function extractParams(paramsNodes) {
  return paramsNodes.map((p) => {
    let node = p;
    let isRest = node.type === "RestElement";
    if (isRest) node = node.argument;
    let name = node.name || (node.left && node.left.name) || "arg";
    let typeAnnotation = typeToString(node.typeAnnotation && node.typeAnnotation.typeAnnotation);
    let kind = isRest ? "rest" : node.type === "AssignmentPattern" ? "default" : "positional";
    return { name, typeAnnotation, kind };
  });
}

function isPublicName(name, exported) {
  if (exported) return true;
  return !name.startsWith("_");
}

function extractFromSource(source) {
  source = source || "";
  let ast;
  try {
    ast = parse(source, {
      sourceType: "module",
      plugins: ["typescript", "jsx", "classProperties", "topLevelAwait"],
      errorRecovery: true,
    });
  } catch (e) {
    return { error: "parse error: " + e.message };
  }

  const functions = {};

  function addFunction(name, node, exported, sourceOverride) {
    if (!name) return;
    const src = sourceOverride !== undefined ? sourceOverride : source.slice(node.start, node.end);
    functions[name] = {
      name,
      params: extractParams(node.params || []),
      source: src,
      isAsync: !!node.async,
      isPublic: isPublicName(name, exported),
    };
  }

  for (const stmt of ast.program.body) {
    let node = stmt;
    let exported = false;
    if (node.type === "ExportNamedDeclaration" && node.declaration) {
      exported = true;
      node = node.declaration;
    } else if (node.type === "ExportDefaultDeclaration") {
      exported = true;
      node = node.declaration;
    }

    if (node.type === "FunctionDeclaration" && node.id) {
      addFunction(node.id.name, node, exported);
    } else if (node.type === "VariableDeclaration") {
      for (const decl of node.declarations) {
        if (
          decl.init &&
          (decl.init.type === "ArrowFunctionExpression" || decl.init.type === "FunctionExpression") &&
          decl.id &&
          decl.id.name
        ) {
          const declText = source.slice(decl.start, decl.end);
          addFunction(decl.id.name, decl.init, exported, `${node.kind} ${declText};`);
        }
      }
    }
  }

  return { functions };
}

function withExecutableSource(fn) {
  if (!fn) return null;
  if (fn.executableSource === undefined) fn.executableSource = stripTypes(fn.source);
  return fn;
}

function normalizeForDiff(src) {
  return (src || "").split(/\s+/).filter(Boolean).join(" ");
}

function diffFunctions(oldSource, newSource) {
  const oldResult = oldSource ? extractFromSource(oldSource) : { functions: {} };
  const newResult = newSource ? extractFromSource(newSource) : { functions: {} };
  if (oldResult.error) return { error: "old: " + oldResult.error };
  if (newResult.error) return { error: "new: " + newResult.error };

  const oldFuncs = oldResult.functions;
  const newFuncs = newResult.functions;
  const names = Array.from(new Set([...Object.keys(oldFuncs), ...Object.keys(newFuncs)])).sort();

  const changes = [];
  for (const name of names) {
    const o = oldFuncs[name];
    const n = newFuncs[name];
    if (!o) {
      changes.push({ name, changeType: "added", old: null, new: n });
    } else if (!n) {
      changes.push({ name, changeType: "removed", old: o, new: null });
    } else if (normalizeForDiff(o.source) !== normalizeForDiff(n.source)) {
      changes.push({ name, changeType: "modified", old: withExecutableSource(o), new: withExecutableSource(n) });
    }
  }
  return { changes };
}

function diffFiles(files) {
  const results = {};
  for (const file of files || []) {
    if (!file || typeof file.path !== "string") continue;
    try {
      results[file.path] = diffFunctions(file.old, file.new);
    } catch (e) {
      results[file.path] = { error: "internal: " + e.message };
    }
  }
  return { results };
}

function main() {
  readStdin().then((input) => {
    let payload;
    try {
      payload = JSON.parse(input);
    } catch (e) {
      process.stdout.write(JSON.stringify({ error: "bad stdin json: " + e.message }));
      return;
    }

    if (Array.isArray(payload.files)) {
      process.stdout.write(JSON.stringify(diffFiles(payload.files)));
      return;
    }

    if ("old" in payload || "new" in payload) {
      process.stdout.write(JSON.stringify(diffFunctions(payload.old, payload.new)));
      return;
    }

    const extracted = extractFromSource(payload.source || "");
    if (extracted.functions) {
      for (const name of Object.keys(extracted.functions)) withExecutableSource(extracted.functions[name]);
    }
    process.stdout.write(JSON.stringify(extracted));
  });
}

main();
