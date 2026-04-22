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
