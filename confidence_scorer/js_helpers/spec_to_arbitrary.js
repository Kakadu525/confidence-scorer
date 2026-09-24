const fc = require("fast-check");

const ALLOWED_KINDS = new Set([
  "integers",
  "floats",
  "text",
  "booleans",
  "binary",
  "none",
  "sampled_from",
  "lists",
  "tuples",
  "dictionaries",
  "one_of",
]);

const INT_EDGES = [-1000, -100, -10, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6, 10, 42, 100, 1000];
const FLOAT_EDGES = [...INT_EDGES.map((n) => n + 0.0), -0.5, -0.001, 0.001, 0.5];

function withEdges(baseArb, edges) {
  if (!edges.length) return baseArb;
  return fc.oneof(...edges.map((e) => fc.constant(e)), baseArb);
}

const TEXT_INTERESTING_CHARS = " \t\n-_./\\:@#'\"0aA".split("");

function textArbitrary(maxLength) {
  return fc.oneof(
    fc.stringOf(fc.constantFrom(...TEXT_INTERESTING_CHARS), { maxLength }),
    fc.string({ maxLength })
  );
}

function specToArbitrary(spec) {
  if (!spec || !ALLOWED_KINDS.has(spec.kind)) {
    throw new Error("strategy kind is not in the allowlist: " + JSON.stringify(spec && spec.kind));
  }
  switch (spec.kind) {
    case "integers": {
      if (spec.min_value !== undefined || spec.max_value !== undefined) {
        const opts = {};
        if (spec.min_value !== undefined) opts.min = spec.min_value;
        if (spec.max_value !== undefined) opts.max = spec.max_value;
        const edges = INT_EDGES.filter(
          (e) => (opts.min === undefined || e >= opts.min) && (opts.max === undefined || e <= opts.max)
        );
        return withEdges(fc.integer(opts), edges);
      }
      return withEdges(fc.integer({ min: -1000, max: 1000 }), INT_EDGES);
    }
    case "floats": {
      if (spec.min_value !== undefined || spec.max_value !== undefined) {
        const opts = { noNaN: true };
        if (spec.min_value !== undefined) opts.min = spec.min_value;
        if (spec.max_value !== undefined) opts.max = spec.max_value;
        return fc.float(opts);
      }
      return withEdges(fc.float({ min: -1000, max: 1000, noNaN: true }), FLOAT_EDGES);
    }
    case "text":
      return textArbitrary(spec.max_size || 50);
    case "booleans":
      return fc.boolean();
    case "binary":
      return fc.uint8Array({ maxLength: spec.max_size || 50 });
    case "none":
      return fc.constant(null);
    case "sampled_from": {
      const values = spec.values || [];
      return values.length ? fc.constantFrom(...values) : fc.constant(null);
    }
    case "lists":
      return fc.array(specToArbitrary(spec.elements), { maxLength: spec.max_size || 8 });
    case "tuples":
      return fc.tuple(...spec.elements.map(specToArbitrary));
    case "dictionaries":
      return fc.dictionary(specToArbitrary(spec.keys), specToArbitrary(spec.values), {
        maxKeys: spec.max_size || 5,
      });
    case "one_of":
      return fc.oneof(...spec.options.map(specToArbitrary));
    default:
      throw new Error("strategy kind is not implemented: " + spec.kind);
  }
}

function typeStringToSpec(t) {
  if (!t) return null;
  t = t.trim();
  if (t === "number") return { kind: "floats" };
  if (t === "string") return { kind: "text" };
  if (t === "boolean") return { kind: "booleans" };
  if (t === "null" || t === "undefined") return { kind: "none" };

  let m = t.match(/^(.+)\[\]$/);
  if (m) {
    const inner = typeStringToSpec(m[1].trim());
    return inner ? { kind: "lists", elements: inner } : null;
  }
  m = t.match(/^Array<(.+)>$/);
  if (m) {
    const inner = typeStringToSpec(m[1].trim());
    return inner ? { kind: "lists", elements: inner } : null;
  }

  if (t.includes("|")) {
    const parts = t.split("|").map((s) => s.trim());
    const specs = parts.map(typeStringToSpec);
    if (specs.every(Boolean)) return { kind: "one_of", options: specs };
    return null;
  }

  m = t.match(/^'([^']*)'$/) || t.match(/^"([^"]*)"$/);
  if (m) return { kind: "sampled_from", values: [m[1]] };
  if (/^-?\d+(\.\d+)?$/.test(t)) return { kind: "sampled_from", values: [Number(t)] };

  return null;
}

function neutralValue(spec) {
  if (!spec) return null;
  switch (spec.kind) {
    case "integers":
      return 0;
    case "floats":
      return 0.0;
    case "text":
      return "";
    case "booleans":
      return false;
    case "binary":
      return new Uint8Array();
    case "none":
      return null;
    case "sampled_from":
      return (spec.values || [null])[0];
    case "lists":
      return [];
    case "tuples":
      return (spec.elements || []).map(neutralValue);
    case "dictionaries":
      return {};
    case "one_of":
      return neutralValue((spec.options || [{ kind: "none" }])[0]);
    default:
      return null;
  }
}

function edgeValues(spec) {
  if (!spec) return [];
  switch (spec.kind) {
    case "integers": {
      const lo = spec.min_value,
        hi = spec.max_value;
      return INT_EDGES.filter((e) => (lo === undefined || e >= lo) && (hi === undefined || e <= hi));
    }
    case "floats": {
      const lo = spec.min_value,
        hi = spec.max_value;
      return FLOAT_EDGES.filter((e) => (lo === undefined || e >= lo) && (hi === undefined || e <= hi));
    }
    case "booleans":
      return [true, false];
    case "text":
      return ["", " ", "a", "a b", "  a  ", "\n", "0", "-"];
    case "none":
      return [null];
    case "sampled_from":
      return spec.values || [];
    default:
      return [];
  }
}

function buildExplicitExamples(orderedParams, maxExamples) {
  maxExamples = maxExamples || 40;
  if (!orderedParams.length) return [];
  const neutral = orderedParams.map((p) => neutralValue(p.spec));
  const examples = [];
  const seen = new Set();

  for (let i = 0; i < orderedParams.length; i++) {
    const values = edgeValues(orderedParams[i].spec);
    for (const v of values) {
      const candidate = neutral.slice();
      candidate[i] = v;
      const key = JSON.stringify(candidate);
      if (seen.has(key)) continue;
      seen.add(key);
      examples.push(candidate);
      if (examples.length >= maxExamples) return examples;
    }
  }
  return examples;
}

module.exports = { specToArbitrary, typeStringToSpec, ALLOWED_KINDS, buildExplicitExamples, textArbitrary };
