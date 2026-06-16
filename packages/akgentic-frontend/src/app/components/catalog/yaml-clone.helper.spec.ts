import yaml from 'js-yaml';

import {
  CloneYamlError,
  extractYamlName,
  extractYamlNamespace,
  extractYamlPublic,
  extractYamlShareable,
  extractYamlUserId,
  rewriteNamespaceInYaml,
  suggestDestName,
  suggestDestNamespace,
} from './yaml-clone.helper';

/**
 * Unit tests for {@link rewriteNamespaceInYaml} — pure-function, no TestBed.
 *
 * Covers Story 11.5 AC 7 / AC 19 — the NFR9 correctness matrix:
 *   1. Multi-kind bundle rewrite.
 *   2. Payload literal preservation (string `src` inside payload is NOT
 *      rewritten).
 *   4. Round-trip structural equality except root namespace.
 *   5. Malformed YAML → CloneYamlError (`yaml-parse-error: ...`).
 *   6. Non-mapping root → CloneYamlError (`bundle root is not a mapping`).
 *   7. Missing `namespace` key → CloneYamlError.
 *   8. Empty destNs → CloneYamlError.
 */
describe('rewriteNamespaceInYaml', () => {
  // Test 1 — multi-kind bundle rewrite.
  it('rewrites root namespace and preserves every entry kind', () => {
    const input = `namespace: src
user_id: null
entries:
  team-1:
    kind: team
    model_type: BaseTeamModel
    payload:
      name: Team One
  agent-1:
    kind: agent
    model_type: BaseAgentModel
    payload:
      role: assistant
  tool-1:
    kind: tool
    model_type: BaseToolModel
    payload:
      name: search
  model-1:
    kind: model
    model_type: ChatModel
    payload:
      provider: openai
  prompt-1:
    kind: prompt
    model_type: PromptModel
    payload:
      text: hello
`;
    const out = rewriteNamespaceInYaml(input, 'dst');
    const parsedOut = yaml.load(out) as Record<string, unknown>;
    const parsedIn = yaml.load(input) as Record<string, unknown>;

    expect(parsedOut['namespace']).toBe('dst');
    expect(parsedOut['user_id']).toBeNull();

    const inEntries = parsedIn['entries'] as Record<string, unknown>;
    const outEntries = parsedOut['entries'] as Record<string, unknown>;
    expect(Object.keys(outEntries).sort()).toEqual(Object.keys(inEntries).sort());

    for (const entryKey of Object.keys(inEntries)) {
      // Every entry's kind / model_type / payload is deep-equal to input.
      expect(JSON.stringify(outEntries[entryKey])).toEqual(
        JSON.stringify(inEntries[entryKey]),
      );
    }
  });

  // Test 2 — payload literal preservation.
  it('preserves payload string literals that happen to mention the source namespace', () => {
    const input = `namespace: src
user_id: null
entries:
  agent-1:
    kind: agent
    model_type: BaseAgentModel
    payload:
      description: "configured for src namespace"
      config:
        name: "src-style name"
`;
    const out = rewriteNamespaceInYaml(input, 'dst');
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['namespace']).toBe('dst');

    const entries = parsed['entries'] as Record<string, unknown>;
    const agent = entries['agent-1'] as Record<string, unknown>;
    const payload = agent['payload'] as Record<string, unknown>;
    expect(payload['description']).toBe('configured for src namespace');
    const cfg = payload['config'] as Record<string, unknown>;
    expect(cfg['name']).toBe('src-style name');
  });

  // Test 4 — round-trip structural equality except root namespace.
  it('produces output that deep-equals a destNs-mutated input clone', () => {
    const input = `namespace: src
user_id: alice
entries:
  team-1:
    kind: team
    model_type: BaseTeamModel
    payload:
      name: Team One
      tags: [x, y]
  agent-1:
    kind: agent
    model_type: BaseAgentModel
    payload:
      role: assistant
`;
    const out = rewriteNamespaceInYaml(input, 'dst');
    const parsedOut = yaml.load(out) as Record<string, unknown>;
    const parsedIn = yaml.load(input) as Record<string, unknown>;
    // Deep-copy input and mutate its root namespace to 'dst'; the result
    // MUST deep-equal the rewrite output.
    const expected = JSON.parse(JSON.stringify(parsedIn));
    expected.namespace = 'dst';
    expect(JSON.stringify(parsedOut)).toEqual(JSON.stringify(expected));
  });

  // Test 5 — malformed YAML → CloneYamlError.
  it('throws CloneYamlError with yaml-parse-error prefix for malformed YAML', () => {
    // A scalar line followed by a stray `:` is a YAMLException in js-yaml.
    const malformed = '{a: [b,\n';
    let caught: unknown = null;
    try {
      rewriteNamespaceInYaml(malformed, 'dst');
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(CloneYamlError);
    expect((caught as CloneYamlError).message).toMatch(/^yaml-parse-error:/);
  });

  // Test 6 — non-mapping root → CloneYamlError.
  it('throws CloneYamlError when the root is a sequence, not a mapping', () => {
    const listRoot = '- item\n- item\n';
    expect(() => rewriteNamespaceInYaml(listRoot, 'dst')).toThrowError(
      CloneYamlError,
      'bundle root is not a mapping',
    );
  });

  // Test 7 — missing namespace key → CloneYamlError.
  it('throws CloneYamlError when the root mapping has no namespace key', () => {
    const noNs = 'user_id: null\nentries: {}\n';
    expect(() => rewriteNamespaceInYaml(noNs, 'dst')).toThrowError(
      CloneYamlError,
      'bundle root has no `namespace` key',
    );
  });

  // Test 8 — empty destNs → CloneYamlError.
  it('throws CloneYamlError when destNs is the empty string', () => {
    const valid = 'namespace: src\nuser_id: null\nentries: {}\n';
    expect(() => rewriteNamespaceInYaml(valid, '')).toThrowError(
      CloneYamlError,
      'destNs must be a non-empty string',
    );
  });
});

describe('extractYamlNamespace', () => {
  it('returns the top-level namespace string on a well-formed bundle', () => {
    const input = 'namespace: foo\nuser_id: null\nentries: {}\n';
    expect(extractYamlNamespace(input)).toBe('foo');
  });

  it('returns null on malformed YAML (defers to the server)', () => {
    const malformed = 'namespace: foo\nentries: [\n  unclosed\n';
    expect(extractYamlNamespace(malformed)).toBeNull();
  });

  it('returns null when the root is not a mapping', () => {
    expect(extractYamlNamespace('- 1\n- 2\n')).toBeNull();
  });

  it('returns null when the mapping has no namespace key', () => {
    expect(extractYamlNamespace('user_id: null\nentries: {}\n')).toBeNull();
  });

  it('returns null when namespace is not a string (e.g. a number)', () => {
    expect(extractYamlNamespace('namespace: 42\nentries: {}\n')).toBeNull();
  });
});

describe('extractYamlUserId', () => {
  it('returns the top-level user_id string on a well-formed bundle', () => {
    const input = 'namespace: foo\nuser_id: alice\nentries: {}\n';
    expect(extractYamlUserId(input)).toBe('alice');
  });

  it('returns null on malformed YAML (defers to the server)', () => {
    const malformed = 'namespace: foo\nentries: [\n  unclosed\n';
    expect(extractYamlUserId(malformed)).toBeNull();
  });

  it('returns null when the root is not a mapping', () => {
    expect(extractYamlUserId('- 1\n- 2\n')).toBeNull();
  });

  it('returns null when the mapping has no user_id key', () => {
    expect(extractYamlUserId('namespace: foo\nentries: {}\n')).toBeNull();
  });

  it('returns null when user_id is null (unknown ownership)', () => {
    expect(extractYamlUserId('namespace: foo\nuser_id: null\nentries: {}\n')).toBeNull();
  });

  it('returns null when user_id is not a string (e.g. a number)', () => {
    expect(extractYamlUserId('namespace: foo\nuser_id: 42\nentries: {}\n')).toBeNull();
  });
});

describe('rewriteNamespaceInYaml — destName parameter', () => {
  it('rewrites root `name` when destName is provided', () => {
    const input = 'namespace: src\nname: Source Display\nentries: {}\n';
    const out = rewriteNamespaceInYaml(input, 'dst', 'Dest Display');
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['namespace']).toBe('dst');
    expect(parsed['name']).toBe('Dest Display');
  });

  it('leaves root `name` unchanged when destName is omitted', () => {
    const input = 'namespace: src\nname: Source Display\nentries: {}\n';
    const out = rewriteNamespaceInYaml(input, 'dst');
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['namespace']).toBe('dst');
    expect(parsed['name']).toBe('Source Display');
  });

  it('adds a root `name` when the bundle has none and destName is provided', () => {
    const input = 'namespace: src\nentries: {}\n';
    const out = rewriteNamespaceInYaml(input, 'dst', 'Dest Display');
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['name']).toBe('Dest Display');
  });

  it('throws CloneYamlError when destName is the empty string', () => {
    const valid = 'namespace: src\nname: Source\nentries: {}\n';
    expect(() => rewriteNamespaceInYaml(valid, 'dst', '')).toThrowError(
      CloneYamlError,
      'destName must be a non-empty string when provided',
    );
  });
});

describe('extractYamlName', () => {
  it('returns the top-level name string on a header-bearing bundle', () => {
    const input = 'namespace: foo\nname: Foo Display\nentries: {}\n';
    expect(extractYamlName(input)).toBe('Foo Display');
  });

  it('returns null when the mapping has no name key', () => {
    expect(extractYamlName('namespace: foo\nentries: {}\n')).toBeNull();
  });

  it('returns null when name is not a string', () => {
    expect(extractYamlName('namespace: foo\nname: 42\nentries: {}\n')).toBeNull();
  });

  it('returns null on malformed YAML', () => {
    expect(extractYamlName('namespace: foo\nentries: [\n  unclosed\n')).toBeNull();
  });

  it('returns null when the root is not a mapping', () => {
    expect(extractYamlName('- 1\n- 2\n')).toBeNull();
  });
});

describe('suggestDestNamespace', () => {
  it('appends a fresh _<5 alphanumerics> suffix when none is present', () => {
    const out = suggestDestNamespace('foo');
    expect(out).toMatch(/^foo_[A-Za-z0-9]{5}$/);
  });

  it('strips a trailing _<5 alphanumerics> suffix and appends a fresh one', () => {
    const out = suggestDestNamespace('foo_a1b2c');
    expect(out).toMatch(/^foo_[A-Za-z0-9]{5}$/);
    // The fresh suffix is almost-certainly different from the source's
    // (collision probability ~1 / 62^5 ≈ 1e-9 — negligible for a unit
    // test). Asserting inequality guards the strip-then-append behaviour.
    expect(out).not.toBe('foo_a1b2c');
  });

  it('does not strip a trailing underscore-suffix of the wrong length', () => {
    // 4 chars — should not match the strip regex; the whole input becomes the base.
    const out = suggestDestNamespace('foo_abcd');
    expect(out).toMatch(/^foo_abcd_[A-Za-z0-9]{5}$/);
  });

  it('does not strip a trailing suffix containing non-alphanumerics', () => {
    const out = suggestDestNamespace('foo_a-b2c');
    expect(out).toMatch(/^foo_a-b2c_[A-Za-z0-9]{5}$/);
  });

  it('produces different suggestions across calls (randomness sanity-check)', () => {
    const a = suggestDestNamespace('foo');
    const b = suggestDestNamespace('foo');
    expect(a).not.toBe(b);
  });
});

describe('suggestDestName', () => {
  it('appends _copy to the source name', () => {
    expect(suggestDestName('Foo Display')).toBe('Foo Display_copy');
  });

  it('handles the empty source name', () => {
    expect(suggestDestName('')).toBe('_copy');
  });
});

// ---------------------------------------------------------------------
// Story 12.2 — shareable / public extractors + flag-write parameters.
// ---------------------------------------------------------------------

describe('extractYamlShareable', () => {
  it('returns true when root shareable is the boolean true', () => {
    expect(
      extractYamlShareable('namespace: foo\nshareable: true\nentries: {}\n'),
    ).toBe(true);
  });

  it('returns false when root shareable is the boolean false', () => {
    expect(
      extractYamlShareable('namespace: foo\nshareable: false\nentries: {}\n'),
    ).toBe(false);
  });

  it('returns null when the shareable key is absent', () => {
    expect(extractYamlShareable('namespace: foo\nentries: {}\n')).toBeNull();
  });

  it('returns null when shareable is not a boolean (string "yes")', () => {
    expect(
      extractYamlShareable('namespace: foo\nshareable: "yes"\nentries: {}\n'),
    ).toBeNull();
  });

  it('returns null when shareable is not a boolean (a number)', () => {
    expect(
      extractYamlShareable('namespace: foo\nshareable: 1\nentries: {}\n'),
    ).toBeNull();
  });

  it('returns null on malformed YAML', () => {
    expect(
      extractYamlShareable('namespace: foo\nentries: [\n  unclosed\n'),
    ).toBeNull();
  });

  it('returns null when the root is a sequence, not a mapping', () => {
    expect(extractYamlShareable('- 1\n- 2\n')).toBeNull();
  });
});

describe('extractYamlPublic', () => {
  it('returns true when root public is the boolean true', () => {
    expect(
      extractYamlPublic('namespace: foo\npublic: true\nentries: {}\n'),
    ).toBe(true);
  });

  it('returns false when root public is the boolean false', () => {
    expect(
      extractYamlPublic('namespace: foo\npublic: false\nentries: {}\n'),
    ).toBe(false);
  });

  it('returns null when the public key is absent', () => {
    expect(extractYamlPublic('namespace: foo\nentries: {}\n')).toBeNull();
  });

  it('returns null when public is not a boolean (string "yes")', () => {
    expect(
      extractYamlPublic('namespace: foo\npublic: "yes"\nentries: {}\n'),
    ).toBeNull();
  });

  it('returns null when public is not a boolean (a number)', () => {
    expect(
      extractYamlPublic('namespace: foo\npublic: 0\nentries: {}\n'),
    ).toBeNull();
  });

  it('returns null on malformed YAML', () => {
    expect(
      extractYamlPublic('namespace: foo\nentries: [\n  unclosed\n'),
    ).toBeNull();
  });

  it('returns null when the root is a sequence, not a mapping', () => {
    expect(extractYamlPublic('- 1\n- 2\n')).toBeNull();
  });
});

describe('rewriteNamespaceInYaml — shareable/public parameters', () => {
  // AC #3 — writes both flags when provided; destName behaviour unchanged.
  it('writes root shareable=true and public=false when both args are provided', () => {
    const out = rewriteNamespaceInYaml(
      'namespace: src\nname: Source\nentries: {}\n',
      'dst',
      'Dest',
      true,
      false,
    );
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['shareable']).toBe(true);
    expect(parsed['public']).toBe(false);
    expect(parsed['namespace']).toBe('dst');
    expect(parsed['name']).toBe('Dest');
  });

  // AC #4 — the critical case: explicit false overwrites a source true for
  // BOTH flags. A dropped false would silently inherit the source value.
  it('writes an explicit false for BOTH flags, overwriting a source true', () => {
    const source =
      'namespace: src\nshareable: true\npublic: true\nentries: {}\n';
    const out = rewriteNamespaceInYaml(source, 'dst', undefined, false, false);
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['shareable']).toBe(false);
    expect(parsed['public']).toBe(false);
  });

  // AC #5 — omitting BOTH flag args leaves both keys untouched.
  it('leaves both keys untouched when neither flag arg is passed', () => {
    const source =
      'namespace: src\nshareable: true\npublic: true\nentries: {}\n';
    const out = rewriteNamespaceInYaml(source, 'dst');
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['shareable']).toBe(true);
    expect(parsed['public']).toBe(true);
  });

  // AC #5 — the two flags are independent: setting shareable=false (4th arg)
  // while omitting the 5th leaves public untouched.
  it('treats the two flags independently — 4th arg false, 5th omitted', () => {
    const source =
      'namespace: src\nshareable: true\npublic: true\nentries: {}\n';
    const out = rewriteNamespaceInYaml(source, 'dst', undefined, false);
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['shareable']).toBe(false);
    expect(parsed['public']).toBe(true);
  });

  // AC #6 — key-add when the source lacks the keys.
  it('adds both keys when the source bundle lacks them', () => {
    const out = rewriteNamespaceInYaml(
      'namespace: src\nentries: {}\n',
      'dst',
      undefined,
      true,
      true,
    );
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['shareable']).toBe(true);
    expect(parsed['public']).toBe(true);
  });
});
