import { TestBed } from '@angular/core/testing';
import { ApiService } from './api.service';
import { FetchService } from './fetch.service';
import { AuthService } from './auth.service';
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
