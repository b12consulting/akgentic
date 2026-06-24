import { TestBed } from '@angular/core/testing';
import { ApiService } from './api.service';
import { FetchService } from './fetch.service';
import { AuthService } from '../auth/auth.service';
import { Router } from '@angular/router';

describe('ApiService', () => {
  let service: ApiService;
  let fetchServiceSpy: jasmine.SpyObj<FetchService>;

  beforeEach(() => {
    fetchServiceSpy = jasmine.createSpyObj('FetchService', ['fetch']);
    fetchServiceSpy.fetch.and.returnValue(Promise.resolve(undefined));

    TestBed.configureTestingModule({
      providers: [
        ApiService,
        { provide: FetchService, useValue: fetchServiceSpy },
        { provide: AuthService, useValue: {} },
        { provide: Router, useValue: {} },
      ],
    });

    service = TestBed.inject(ApiService);
  });

  describe('sendMessageFromTo', () => {
    it('should call fetch with correct URL and body', async () => {
      await service.sendMessageFromTo('team-1', '@Developer', '@Manager', 'hello');

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toContain('/teams/team-1/message/from/@Developer/to/@Manager');
      expect(callArgs.options?.method).toBe('POST');
      expect(callArgs.options?.body).toBe(JSON.stringify({ content: 'hello' }));
      expect(callArgs.options?.headers).toEqual({ 'Content-Type': 'application/json' });
    });

    it('should return void (resolves to undefined)', async () => {
      const result = await service.sendMessageFromTo('t1', '@A', '@B', 'msg');
      expect(result).toBeUndefined();
    });
  });

  describe('getNamespaces (Story 1.9)', () => {
    it('hits GET /catalog/namespaces and returns the array', async () => {
      const payload = [
        { namespace: 'agent-team-v1', name: 'Agent Team', description: 'Default' },
      ];
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(payload));

      const result = await service.getNamespaces();

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/catalog\/namespaces$/);
      expect(result).toEqual(payload);
    });
  });

  describe('getNamespaces ?all=true (Story 14.4 AC5, AC16)', () => {
    it('appends ?all=true when opts.all is truthy', async () => {
      await service.getNamespaces({ all: true });

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/admin\/catalog\/namespaces\?all=true$/);
    });

    it('issues the bare URL (no query) when opts.all is false', async () => {
      await service.getNamespaces({ all: false });

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/admin\/catalog\/namespaces$/);
      expect(callArgs.url).not.toContain('?all');
    });

    it('issues the bare URL (no query) when opts is omitted', async () => {
      await service.getNamespaces();

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/admin\/catalog\/namespaces$/);
      expect(callArgs.url).not.toContain('?all');
    });
  });

  describe('createTeam (Story 1.9)', () => {
    it('POSTs {catalog_namespace, params:{}} — not catalog_entry_id', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve({} as any));

      await service.createTeam('agent-team-v1');

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/teams$/);
      expect(callArgs.options?.method).toBe('POST');
      const body = JSON.parse(callArgs.options?.body as string);
      expect(body).toEqual({ catalog_namespace: 'agent-team-v1', params: {} });
    });
  });

  describe('exportNamespace (Story 11.1)', () => {
    it('GETs /admin/catalog/namespace/{ns}/export with responseType: text', async () => {
      const yamlText = 'namespace: foo\nname: My NS\n';
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(yamlText));

      const result = await service.exportNamespace('foo');

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/admin\/catalog\/namespace\/foo\/export$/);
      expect(callArgs.options?.method).toBeUndefined(); // defaults to GET
      expect(callArgs.responseType).toBe('text');
      expect(result).toBe(yamlText);
    });

    it('(Story 14.4 AC8) appends ?all=true for an admin foreign-open', async () => {
      const yamlText = 'namespace: foo\n';
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(yamlText));

      await service.exportNamespace('foo', { all: true });

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(
        /\/admin\/catalog\/namespace\/foo\/export\?all=true$/,
      );
      expect(callArgs.responseType).toBe('text');
    });

    it('(Story 14.4 AC8) issues the bare export URL when all is false/omitted', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve('x'));

      await service.exportNamespace('foo', { all: false });

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(
        /\/admin\/catalog\/namespace\/foo\/export$/,
      );
      expect(callArgs.url).not.toContain('?all');
    });
  });

  describe('importNamespace (Story 11.1)', () => {
    it('POSTs raw YAML with Content-Type: application/yaml and returns Entry[]', async () => {
      const yaml = 'namespace: foo\n';
      const entries = [
        {
          id: 'team-1',
          kind: 'team',
          namespace: 'foo',
          model_type: 'akgentic.team.TeamConfig',
          description: '',
          payload: {},
        },
      ];
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(entries));

      const result = await service.importNamespace(yaml);

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/admin\/catalog\/namespace\/import$/);
      expect(callArgs.options?.method).toBe('POST');
      // Body MUST be the raw string, not JSON-stringified
      expect(callArgs.options?.body).toBe(yaml);
      expect(callArgs.options?.headers).toEqual({
        'Content-Type': 'application/yaml',
      });
      // No responseType override — defaults to JSON for the Entry[] response
      expect(callArgs.responseType).toBeUndefined();
      expect(result).toEqual(entries as any);
    });
  });

  describe('validatePersistedNamespace (Story 11.1)', () => {
    it('GETs /admin/catalog/namespace/{ns}/validate and returns the report', async () => {
      const report = {
        namespace: 'foo',
        ok: true,
        global_errors: [],
        entry_issues: [],
      };
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(report));

      const result = await service.validatePersistedNamespace('foo');

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(
        /\/admin\/catalog\/namespace\/foo\/validate$/
      );
      expect(callArgs.options?.method).toBeUndefined(); // defaults to GET
      expect(callArgs.responseType).toBeUndefined(); // JSON by default
      expect(result).toEqual(report);
    });
  });

  describe('validateNamespaceBuffer (Story 11.1)', () => {
    it('POSTs raw YAML with Content-Type: application/yaml and returns the report', async () => {
      const yaml = 'namespace: foo\n';
      const report = {
        namespace: 'foo',
        ok: false,
        global_errors: ['boom'],
        entry_issues: [],
      };
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(report));

      const result = await service.validateNamespaceBuffer(yaml);

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/admin\/catalog\/namespace\/validate$/);
      expect(callArgs.options?.method).toBe('POST');
      expect(callArgs.options?.body).toBe(yaml);
      expect(callArgs.options?.headers).toEqual({
        'Content-Type': 'application/yaml',
      });
      expect(result).toEqual(report);
    });
  });

  describe('deleteNamespace (Story 14.1)', () => {
    it('DELETEs /admin/catalog/namespace/{ns} and resolves void', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(undefined));

      const result = await service.deleteNamespace('foo');

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/admin\/catalog\/namespace\/foo$/);
      expect(callArgs.options?.method).toBe('DELETE');
      // The panel owns the success toast — no successMessage passed here.
      expect(callArgs.successMessage).toBeUndefined();
      expect(result).toBeUndefined();
    });
  });

  describe('getAgentStates (Story 25-1, ADR-020 §2)', () => {
    it('GETs /teams/:id/agent-states and returns the states array', async () => {
      const states = [
        {
          agent_id: '11111111-1111-1111-1111-111111111111',
          name: '@Researcher',
          state: { backstory: 'A seasoned researcher.' },
          updated_at: '2026-06-18T00:00:00Z',
        },
      ];
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve({ states }));

      const result = await service.getAgentStates('team-1');

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/teams\/team-1\/agent-states$/);
      expect(callArgs.options?.method).toBeUndefined(); // defaults to GET
      expect(result).toEqual(states);
    });

    it('returns [] when the response body is absent (undefined)', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(undefined));

      const result = await service.getAgentStates('team-1');

      expect(result).toEqual([]);
    });

    it('returns [] when the response has no states key', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve({} as any));

      const result = await service.getAgentStates('team-1');

      expect(result).toEqual([]);
    });
  });

  describe('getTeamsPage (Story 28.1)', () => {
    const makeResponse = () => ({
      teams: [
        {
          team_id: 't1',
          name: 'Alpha',
          status: 'running',
          user_id: 'u1',
          created_at: '2026-06-20T10:00:00Z',
          updated_at: '2026-06-20T10:00:00Z',
        },
      ],
      total_count: 42,
    });

    it('(AC5a) issues the bare /teams URL (no query) when both args omitted', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(makeResponse()));

      await service.getTeamsPage();

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/teams$/);
      expect(callArgs.url).not.toContain('?');
    });

    it('(AC5b) appends page= when only page is provided', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(makeResponse()));

      await service.getTeamsPage(2);

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/teams\?/);
      expect(callArgs.url).toContain('page=2');
      expect(callArgs.url).not.toContain('size=');
    });

    it('(AC5c) appends both page= and size= when both provided', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(makeResponse()));

      await service.getTeamsPage(2, 250);

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toContain('page=2');
      expect(callArgs.url).toContain('size=250');
    });

    it('(AC5c) sends size even when only size is provided', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(makeResponse()));

      await service.getTeamsPage(undefined, 250);

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toContain('size=250');
      expect(callArgs.url).not.toContain('page=');
    });

    it('(AC5d) returns a TeamPage with total_count and teams mapped via toTeamContext', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(makeResponse()));

      const result = await service.getTeamsPage(1, 250);

      expect(result.total_count).toBe(42);
      expect(result.teams.length).toBe(1);
      // toTeamContext slims TeamResponse → TeamContext (drops user_id, adds
      // config_name/description); assert the mapped view-model shape.
      const team = result.teams[0];
      expect(team.team_id).toBe('t1');
      expect(team.config_name).toBe('Alpha');
      expect(team.description).toBeNull();
      expect((team as unknown as Record<string, unknown>)['user_id']).toBeUndefined();
    });

    it('(AC5e) defaults to teams:[] and total_count:0 on a missing/empty body', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(undefined));

      const result = await service.getTeamsPage();

      expect(result.teams).toEqual([]);
      expect(result.total_count).toBe(0);
    });
  });

  describe('sendMessage (existing)', () => {
    it('should broadcast when no agentName provided', async () => {
      await service.sendMessage('team-1', 'broadcast msg');

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toMatch(/\/teams\/team-1\/message$/);
    });

    it('should delegate to sendMessageTo when agentName provided', async () => {
      await service.sendMessage('team-1', 'targeted msg', '@Manager');

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toContain('/teams/team-1/message/@Manager');
    });
  });
});
