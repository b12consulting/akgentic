import { TestBed } from '@angular/core/testing';
import { environment } from '../../environments/environment';
import { ConfigService } from './config.service';
import { FetchService } from './fetch.service';
import {
  MAX_UPLOAD_SIZE_BYTES,
  WorkspaceService,
  WorkspaceTreeResponse,
} from './workspace.service';

// Helper: build a stub `Response`-shaped object returned by `spyOn(window, 'fetch')`.
// We only stub the subset of `Response` that `getFileContent` actually reads:
// `ok`, `statusText`, `arrayBuffer()`.
function mockFetchResponse(opts: {
  ok: boolean;
  statusText?: string;
  arrayBuffer?: () => Promise<ArrayBuffer>;
}): Response {
  return {
    ok: opts.ok,
    statusText: opts.statusText ?? '',
    arrayBuffer: opts.arrayBuffer ?? (() => Promise.resolve(new ArrayBuffer(0))),
  } as unknown as Response;
}

function encodeUtf8(text: string): ArrayBuffer {
  const encoder = new TextEncoder();
  const view = encoder.encode(text);
  // Copy into a fresh ArrayBuffer so slices align cleanly.
  const buffer = new ArrayBuffer(view.byteLength);
  new Uint8Array(buffer).set(view);
  return buffer;
}

describe('WorkspaceService', () => {
  let service: WorkspaceService;
  let fetchServiceSpy: jasmine.SpyObj<FetchService>;

  beforeEach(() => {
    fetchServiceSpy = jasmine.createSpyObj('FetchService', [
      'fetch',
      'showNotification',
    ]);
    fetchServiceSpy.fetch.and.returnValue(Promise.resolve(undefined));

    TestBed.configureTestingModule({
      providers: [
        WorkspaceService,
        { provide: FetchService, useValue: fetchServiceSpy },
        { provide: ConfigService, useValue: { api: environment.api, hideLogin: environment.hideLogin } },
      ],
    });

    service = TestBed.inject(WorkspaceService);
  });

  describe('getWorkspaceTree', () => {
    it('returns a lazy FileNode[] for a root listing', async () => {
      const response: WorkspaceTreeResponse = {
        team_id: 't1',
        path: '',
        entries: [
          { name: 'a.md', is_dir: false, size: 10 },
          { name: 'sub', is_dir: true, size: 0 },
        ],
      };
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(response));

      const result = await service.getWorkspaceTree('p1');

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toBe(
        `${environment.api}/workspace/p1/tree?path=`
      );

      expect(result).toEqual([
        {
          name: 'a.md',
          path: 'a.md',
          type: 'file',
          size: 10,
          extension: '.md',
        },
        {
          name: 'sub',
          path: 'sub',
          type: 'directory',
          size: 0,
          extension: undefined,
        },
      ]);
      expect(result[0].children).toBeUndefined();
      expect(result[1].children).toBeUndefined();
    });

    it('prefixes returned node paths with the parent path for nested listings', async () => {
      const response: WorkspaceTreeResponse = {
        team_id: 't1',
        path: 'src/services',
        entries: [
          { name: 'a.ts', is_dir: false, size: 20 },
          { name: 'nested', is_dir: true, size: 0 },
        ],
      };
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(response));

      const result = await service.getWorkspaceTree('p1', 'src/services');

      const callArgs = fetchServiceSpy.fetch.calls.first().args[0];
      expect(callArgs.url).toContain('?path=src%2Fservices');
      expect(result.map((n) => n.path)).toEqual([
        'src/services/a.ts',
        'src/services/nested',
      ]);
    });

    it('extracts extension correctly for edge-case filenames', async () => {
      const response: WorkspaceTreeResponse = {
        team_id: 't1',
        path: '',
        entries: [
          { name: 'Makefile', is_dir: false, size: 5 },
          { name: '.env', is_dir: false, size: 3 },
          { name: 'archive.tar.gz', is_dir: false, size: 99 },
        ],
      };
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(response));

      const result = await service.getWorkspaceTree('p1');

      expect(result[0].extension).toBeUndefined();
      expect(result[1].extension).toBe('.env');
      expect(result[2].extension).toBe('.gz');
    });

    it('propagates HTTP errors from FetchService.fetch', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.reject(new Error('500')));

      await expectAsync(service.getWorkspaceTree('p1')).toBeRejectedWithError(
        '500'
      );
    });

    it('throws on malformed response (missing entries[])', async () => {
      fetchServiceSpy.fetch.and.returnValue(
        Promise.resolve({ team_id: 't1', path: '' } as unknown as WorkspaceTreeResponse)
      );

      await expectAsync(service.getWorkspaceTree('p1')).toBeRejectedWithError(
        /Malformed workspace tree response/
      );
    });
  });

  describe('getFileContent', () => {
    it('decodes UTF-8 text successfully', async () => {
      const buffer = encodeUtf8('hello world');
      spyOn(window, 'fetch').and.returnValue(
        Promise.resolve(
          mockFetchResponse({ ok: true, arrayBuffer: () => Promise.resolve(buffer) })
        )
      );

      const result = await service.getFileContent('p1', 'a.md');

      expect(result).toEqual({ content: 'hello world', type: 'text' });
    });

    it('classifies invalid UTF-8 bytes as binary', async () => {
      const buffer = new Uint8Array([0xff, 0xfe, 0x00, 0x80]).buffer;
      spyOn(window, 'fetch').and.returnValue(
        Promise.resolve(
          mockFetchResponse({ ok: true, arrayBuffer: () => Promise.resolve(buffer) })
        )
      );

      const result = await service.getFileContent('p1', 'image.png');

      expect(result).toEqual({
        content: null,
        type: 'binary',
        message: 'Binary file cannot be displayed',
      });
    });

    it('treats empty files as empty text', async () => {
      spyOn(window, 'fetch').and.returnValue(
        Promise.resolve(
          mockFetchResponse({
            ok: true,
            arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
          })
        )
      );

      const result = await service.getFileContent('p1', 'empty.txt');

      expect(result).toEqual({ content: '', type: 'text' });
    });

    it('throws when response is non-ok', async () => {
      spyOn(window, 'fetch').and.returnValue(
        Promise.resolve(mockFetchResponse({ ok: false, statusText: 'Not Found' }))
      );

      await expectAsync(
        service.getFileContent('p1', 'missing')
      ).toBeRejectedWithError('Not Found');
    });

    it('uses the correct URL with encoded path', async () => {
      const fetchSpy = spyOn(window, 'fetch').and.returnValue(
        Promise.resolve(
          mockFetchResponse({
            ok: true,
            arrayBuffer: () => Promise.resolve(new ArrayBuffer(0)),
          })
        )
      );

      await service.getFileContent('p1', 'sub/a.md');

      expect(fetchSpy).toHaveBeenCalledTimes(1);
      const callArgs = fetchSpy.calls.first().args;
      expect(callArgs[0]).toBe(
        `${environment.api}/workspace/p1/file?path=sub%2Fa.md`
      );
    });
  });

  describe('getDownloadUrl', () => {
    it('returns the single-endpoint /file URL', () => {
      const url = service.getDownloadUrl('p1', 'src/a.md');
      expect(url).toBe(
        `${environment.api}/workspace/p1/file?path=src%2Fa.md`
      );
    });
  });

  describe('uploadFiles', () => {
    it('issues sequential per-file POSTs with v2 field names', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(undefined));
      const f1 = new File(['a'], 'a.txt');
      const f2 = new File(['b'], 'b.txt');

      await service.uploadFiles('p1', [f1, f2], 'docs');

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(2);

      const [call1, call2] = fetchServiceSpy.fetch.calls.all();
      for (const [i, call] of [call1, call2].entries()) {
        const args = call.args[0];
        expect(args.url).toBe(`${environment.api}/workspace/p1/file`);
        expect(args.options?.method).toBe('POST');
        const body = args.options?.body as FormData;
        expect(body instanceof FormData).toBe(true);
        const name = i === 0 ? 'a.txt' : 'b.txt';
        expect(body.get('path')).toBe(`docs/${name}`);
        const fileField = body.get('file') as File;
        expect(fileField instanceof File).toBe(true);
        expect(fileField.name).toBe(name);
      }
    });

    it('uses the bare filename when targetPath is omitted', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(undefined));
      const f1 = new File(['a'], 'root.txt');

      await service.uploadFiles('p1', [f1]);

      const body = fetchServiceSpy.fetch.calls.first().args[0].options
        ?.body as FormData;
      expect(body.get('path')).toBe('root.txt');
    });

    it('halts on the first failing upload (no further HTTP calls)', async () => {
      fetchServiceSpy.fetch.and.callFake(() => {
        const callCount = fetchServiceSpy.fetch.calls.count();
        if (callCount === 2) {
          return Promise.reject(new Error('boom'));
        }
        return Promise.resolve(undefined);
      });

      const f1 = new File(['a'], 'a.txt');
      const f2 = new File(['b'], 'b.txt');
      const f3 = new File(['c'], 'c.txt');

      await expectAsync(
        service.uploadFiles('p1', [f1, f2, f3])
      ).toBeRejectedWithError('boom');

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(2);
    });

    it('rejects pre-flight when any file exceeds MAX_UPLOAD_SIZE_BYTES', async () => {
      const big = {
        name: 'big.bin',
        size: MAX_UPLOAD_SIZE_BYTES + 1,
      } as unknown as File;

      await expectAsync(service.uploadFiles('p1', [big])).toBeRejectedWithError(
        /exceeds the 10 MB workspace upload limit/
      );

      expect(fetchServiceSpy.fetch).not.toHaveBeenCalled();
      expect(fetchServiceSpy.showNotification).toHaveBeenCalledTimes(1);
      const [msg, severity] = fetchServiceSpy.showNotification.calls.first()
        .args;
      expect(msg).toContain('big.bin');
      expect(severity).toBe('error');
    });

    it('allows the exact 10 MB boundary (strict greater-than)', async () => {
      fetchServiceSpy.fetch.and.returnValue(Promise.resolve(undefined));
      const boundary = {
        name: 'edge.bin',
        size: MAX_UPLOAD_SIZE_BYTES,
      } as unknown as File;

      await service.uploadFiles('p1', [boundary]);

      expect(fetchServiceSpy.fetch).toHaveBeenCalledTimes(1);
    });
  });
});
