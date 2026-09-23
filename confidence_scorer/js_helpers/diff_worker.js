#!/usr/bin/env node
const fc = require("fast-check");
const { specToArbitrary, buildExplicitExamples } = require("./spec_to_arbitrary");

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => (data += chunk));
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

function loadFn(source, name) {
  const body = `${source}\nreturn typeof ${name} !== "undefined" ? ${name} : undefined;`;
  // eslint-disable-next-line no-new-func
  const factory = new Function(body);
  return factory();
}

function reprSafe(v) {
  try {
    return JSON.stringify(v);
  } catch (e) {
    return String(v);
  }
}

function cloneArgs(args) {
  try {
    return structuredClone(args);
  } catch (e) {
    try {
      return JSON.parse(JSON.stringify(args));
    } catch (e2) {
      return args;
    }
  }
}

function deepEqual(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null) return a === b;
  if (typeof a === "number" && typeof b === "number") {
    if (Number.isNaN(a) && Number.isNaN(b)) return true;
    return Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
  }
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, b[i]));
  }
  if (typeof a === "object") {
    const ka = Object.keys(a).sort();
    const kb = Object.keys(b).sort();
    if (ka.length !== kb.length || ka.some((k, i) => k !== kb[i])) return false;
    return ka.every((k) => deepEqual(a[k], b[k]));
  }
  return a === b;
}

function callSafe(fn, args) {
  try {
    return { value: fn(...cloneArgs(args)), error: null };
  } catch (e) {
    return { value: undefined, error: e };
  }
}

function isSelfConsistent(fn, args, attempts) {
  attempts = attempts || 3;
  const first = callSafe(fn, args);
  for (let i = 1; i < attempts; i++) {
    const again = callSafe(fn, args);
    if (!!first.error !== !!again.error) return false;
    if (!first.error && !deepEqual(first.value, again.value)) return false;
  }
  return true;
}

function runOne(task, maxExamples, seed, perFunctionTimeoutMs) {
  let oldFn, newFn;
  try {
    oldFn = loadFn(task.oldSource, task.name);
    newFn = loadFn(task.newSource, task.name);
  } catch (e) {
    return { name: task.name, status: "error", reason: "load failed: " + e.message };
  }
  if (typeof oldFn !== "function" || typeof newFn !== "function") {
    return { name: task.name, status: "error", reason: "функция не найдена после загрузки" };
  }

  let arbs;
  try {
    arbs = task.params.map((p) => specToArbitrary(p.spec));
  } catch (e) {
    return { name: task.name, status: "skipped", reason: "неподдерживаемый generator input: " + e.message };
  }

  if (arbs.length === 0) {
    arbs = [fc.constant(undefined)];
  }

  let lastMismatch = null;

  const predicate = (...args) => {
    if (task.params.length === 0) args = [];
    const oldOutcome = callSafe(oldFn, args);
    const newOutcome = callSafe(newFn, args);

    if (!!oldOutcome.error !== !!newOutcome.error) {
      lastMismatch = {
        args,
        oldRepr: oldOutcome.error ? "throws " + oldOutcome.error.message : reprSafe(oldOutcome.value),
        newRepr: newOutcome.error ? "throws " + newOutcome.error.message : reprSafe(newOutcome.value),
        reason: "старая и новая версия расходятся в том, бросают ли они исключение",
      };
      return false;
    }
    if (!oldOutcome.error && !deepEqual(oldOutcome.value, newOutcome.value)) {
      lastMismatch = {
        args,
        oldRepr: reprSafe(oldOutcome.value),
        newRepr: reprSafe(newOutcome.value),
        reason: "старая и новая версия возвращают разные результаты на одном входе",
      };
      return false;
    }
    return true;
  };

  let report;
  try {
    const explicitExamples = task.params.length ? buildExplicitExamples(task.params) : [];
    const options = {
      numRuns: maxExamples,
      examples: explicitExamples,
      endOnFailure: true,
      interruptAfterTimeLimit: perFunctionTimeoutMs,
      markInterruptAsFailure: false,
    };
    if (seed !== null && seed !== undefined) options.seed = seed;
    report = fc.check(fc.property(...arbs, predicate), options);
  } catch (e) {
    return { name: task.name, status: "error", reason: "fast-check error: " + e.message };
  }

  if (!report.failed) {
    if (report.interrupted) {
      return {
        name: task.name,
        status: "error",
        reason: `таймаут ${Math.round(perFunctionTimeoutMs / 1000)}s (проверено ${report.numRuns} входов)`,
      };
    }
    return { name: task.name, status: "passed" };
  }

  const mismatch = lastMismatch || {
    args: report.counterexample,
    oldRepr: null,
    newRepr: null,
    reason: "counterexample found",
  };

  if (!isSelfConsistent(oldFn, mismatch.args) || !isSelfConsistent(newFn, mismatch.args)) {
    return {
      name: task.name,
      status: "skipped",
      reason: "функция недетерминирована (разные результаты на одном входе), differential testing неприменим",
    };
  }

  return {
    name: task.name,
    status: "failed",
    reason: mismatch.reason,
    args: mismatch.args,
    oldRepr: mismatch.oldRepr,
    newRepr: mismatch.newRepr,
  };
}

async function main() {
  let batch;
  try {
    batch = JSON.parse(await readStdin());
  } catch (e) {
    process.stdout.write(JSON.stringify({ results: [], error: "bad stdin json: " + e.message }));
    return;
  }

  const maxExamples = batch.maxExamples || 50;
  const seed = batch.seed === undefined ? null : batch.seed;
  const perFunctionTimeoutMs = (batch.perFunctionTimeoutS || 15) * 1000;
  const results = (batch.functions || []).map((task) => {
    try {
      return runOne(task, maxExamples, seed, perFunctionTimeoutMs);
    } catch (e) {
      return { name: (task && task.name) || "?", status: "error", reason: "worker crash: " + e.message };
    }
  });
  process.stdout.write(JSON.stringify({ results }));
}

main();
