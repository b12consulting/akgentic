import yaml from 'js-yaml';

import {
  CloneYamlError,
  extractYamlNamespace,
  rewriteNamespaceInYaml,
} from './yaml-clone.helper';

/**
 * Unit tests for {@link rewriteNamespaceInYaml} — pure-function, no TestBed.
 *
 * Covers Story 11.5 AC 7 / AC 19 — the NFR9 correctness matrix:
 *   1. Multi-kind bundle rewrite.
 *   2. Payload literal preservation (string `src` inside payload is NOT
 *      rewritten).
 *   3. `parent_namespace` non-rewrite (lineage pointer untouched).
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

  // Test 3 — parent_namespace is NOT rewritten.
  it('does NOT rewrite per-entry parent_namespace lineage pointers', () => {
    const input = `namespace: src
user_id: null
entries:
  team-1:
    kind: team
    model_type: BaseTeamModel
    parent_namespace: src-parent
    parent_id: ancestor-team
    payload:
      name: Team One
`;
    const out = rewriteNamespaceInYaml(input, 'dst');
    const parsed = yaml.load(out) as Record<string, unknown>;
    expect(parsed['namespace']).toBe('dst');

    const entries = parsed['entries'] as Record<string, unknown>;
    const team = entries['team-1'] as Record<string, unknown>;
    expect(team['parent_namespace']).toBe('src-parent');
    expect(team['parent_id']).toBe('ancestor-team');
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
    parent_namespace: src-parent
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
