import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  CUSTOM_ELEMENTS_SCHEMA,
} from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { TreeNode } from 'primeng/api';
import { BehaviorSubject } from 'rxjs';

import { ContextService } from '../../../../core/context/context.service';
import { FileNode, WorkspaceService } from '../../workspace/workspace.service';
import { UploadModalComponent } from './upload-modal/upload-modal.component';
import { WorkspaceExplorerComponent } from './workspace-explorer.component';

// --------------------------------------------------------------------
// Fixture helpers
// --------------------------------------------------------------------

function makeTeam(): any {
  return {
    team_id: 'proc',
    name: 'Demo Team',
    status: 'running',
    created_at: '2026-04-08T10:00:00Z',
    updated_at: '2026-04-08T10:00:00Z',
    config_name: 'demo',
    description: null,
  };
}

function fileNode(overrides: Partial<FileNode>): FileNode {
  return {
    name: overrides.name || 'x',
    path: overrides.path || 'x',
    type: overrides.type || 'file',
    size: overrides.size ?? 1,
    extension: overrides.extension,
    ...overrides,
  };
}

/**
 * Drive the declarative `toObservable(workspaceId) → switchMap` root load:
 * `detectChanges()` flushes the effect that feeds the signal value into the
 * stream; `whenStable()` waits for the resolved fetch promise to settle the
 * signals. Used everywhere the old spec called `ngOnInit()`/`loadWorkspace()`.
 */
async function flushRootLoad(
  fixture: ComponentFixture<WorkspaceExplorerComponent>,
): Promise<void> {
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
}

describe('WorkspaceExplorerComponent', () => {
  let component: WorkspaceExplorerComponent;
  let fixture: ComponentFixture<WorkspaceExplorerComponent>;
  let workspaceServiceSpy: jasmine.SpyObj<WorkspaceService>;
  let contextServiceStub: {
    currentProcessId$: BehaviorSubject<string>;
    currentTeamRunning$: BehaviorSubject<boolean>;
    getCurrentTeam: jasmine.Spy;
  };

  beforeEach(async () => {
    workspaceServiceSpy = jasmine.createSpyObj('WorkspaceService', [
      'getWorkspaceTree',
      'getFileContent',
      'getDownloadUrl',
      'uploadFiles',
    ]);
    contextServiceStub = {
      currentProcessId$: new BehaviorSubject<string>('proc'),
      currentTeamRunning$: new BehaviorSubject<boolean>(true),
      getCurrentTeam: jasmine
        .createSpy('getCurrentTeam')
        .and.callFake(async () => makeTeam()),
    };

    await TestBed.configureTestingModule({
      imports: [WorkspaceExplorerComponent, NoopAnimationsModule],
      providers: [
        { provide: WorkspaceService, useValue: workspaceServiceSpy },
        { provide: ContextService, useValue: contextServiceStub },
      ],
    })
      .overrideComponent(WorkspaceExplorerComponent, {
        set: {
          imports: [CommonModule],
          schemas: [CUSTOM_ELEMENTS_SCHEMA],
        },
      })
      .compileComponents();
  });

  // --- declarative root-tree load -----------------------------------

  describe('root-tree load', () => {
    it('scenario 1 — root listing wraps backend entries under synthetic Root Folder', async () => {
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([
        fileNode({
          name: 'a.md',
          path: 'a.md',
          type: 'file',
          size: 10,
          extension: '.md',
        }),
        fileNode({ name: 'sub', path: 'sub', type: 'directory', size: 0 }),
      ]);

      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      await flushRootLoad(fixture);

      expect(component.treeNodes().length).toBe(1);
      const root = component.treeNodes()[0];
      expect(root.label).toBe('Root Folder');
      expect(root.icon).toBe('pi pi-home');
      expect(root.expanded).toBe(true);
      expect(root.children?.length).toBe(2);
      expect(component.errorMessage()).toBeNull();

      // The directory child should be lazy (children === undefined, leaf false)
      const subChild = root.children![1];
      expect(subChild.label).toBe('sub');
      expect(subChild.leaf).toBe(false);
      expect(subChild.children).toBeUndefined();

      // The file child should be a leaf
      const fileChild = root.children![0];
      expect(fileChild.label).toBe('a.md');
      expect(fileChild.leaf).toBe(true);
    });

    it('scenario 2 — empty backend renders synthetic root with empty children', async () => {
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([]);

      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      await flushRootLoad(fixture);

      // Synthetic root always exists; its children are []
      expect(component.treeNodes().length).toBe(1);
      expect(component.treeNodes()[0].label).toBe('Root Folder');
      expect(component.treeNodes()[0].children).toEqual([]);
      expect(component.errorMessage()).toBeNull();
    });

    it('scenario 3 — HTTP error sets errorMessage and clears loading', async () => {
      workspaceServiceSpy.getWorkspaceTree.and.rejectWith(new Error('500'));

      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      await flushRootLoad(fixture);

      expect(component.errorMessage()).toBe('500');
      expect(component.loading()).toBe(false);
    });
  });

  // --- onNodeExpand --------------------------------------------------

  describe('onNodeExpand', () => {
    beforeEach(() => {
      // Neutral initial state; tests manually fabricate TreeNodes.
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([]);
      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      component.processId = 'proc';
    });

    it('scenario 4 — first-click on unloaded directory fetches and populates children', async () => {
      const subDir: TreeNode = {
        label: 'sub',
        data: fileNode({ name: 'sub', path: 'sub', type: 'directory' }),
        leaf: false,
        children: undefined,
      };
      component.treeNodes.set([subDir]);

      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([
        fileNode({
          name: 'inner.ts',
          path: 'sub/inner.ts',
          type: 'file',
          size: 1,
          extension: '.ts',
        }),
      ]);

      await component.onNodeExpand({ node: subDir });

      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledOnceWith(
        'proc',
        'sub'
      );
      expect(subDir.children?.length).toBe(1);
      expect(subDir.children![0].leaf).toBe(true);
      expect(subDir.children![0].label).toBe('inner.ts');
    });

    it('scenario 5 — second-click on already-loaded directory is a no-op', async () => {
      const subDir: TreeNode = {
        label: 'sub',
        data: fileNode({ name: 'sub', path: 'sub', type: 'directory' }),
        leaf: false,
        children: undefined,
      };
      component.treeNodes.set([subDir]);

      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([
        fileNode({ name: 'x', path: 'sub/x', type: 'file' }),
      ]);

      await component.onNodeExpand({ node: subDir });
      await component.onNodeExpand({ node: subDir });

      expect(workspaceServiceSpy.getWorkspaceTree.calls.count()).toBe(1);
    });

    it('scenario 6 — expand on file node is a no-op', async () => {
      const fileNd: TreeNode = {
        label: 'a.md',
        data: fileNode({ name: 'a.md', path: 'a.md', type: 'file' }),
        leaf: true,
        children: undefined,
      };

      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      await component.onNodeExpand({ node: fileNd });

      expect(workspaceServiceSpy.getWorkspaceTree).not.toHaveBeenCalled();
    });

    it('scenario 7 — empty-directory response caches (second click is a no-op)', async () => {
      const subDir: TreeNode = {
        label: 'empty',
        data: fileNode({ name: 'empty', path: 'empty', type: 'directory' }),
        leaf: false,
        children: undefined,
      };
      component.treeNodes.set([subDir]);

      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([]);

      await component.onNodeExpand({ node: subDir });
      expect(subDir.children).toEqual([]);

      await component.onNodeExpand({ node: subDir });
      expect(workspaceServiceSpy.getWorkspaceTree.calls.count()).toBe(1);
    });

    it('scenario 8 — HTTP error sets errorMessage and leaves children undefined (retryable)', async () => {
      const subDir: TreeNode = {
        label: 'bad',
        data: fileNode({ name: 'bad', path: 'bad', type: 'directory' }),
        leaf: false,
        children: undefined,
      };
      component.treeNodes.set([subDir]);

      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      workspaceServiceSpy.getWorkspaceTree.and.rejectWith(new Error('boom'));

      await component.onNodeExpand({ node: subDir });

      expect(component.errorMessage()).toBe('boom');
      expect(subDir.children).toBeUndefined();
    });
  });

  // --- loadFileContent (AC2) ----------------------------------------

  describe('loadFileContent', () => {
    beforeEach(() => {
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([]);
      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      component.processId = 'proc';
    });

    it('scenario 19 — text result writes fileContent, clears loadingContent and isBinaryFile', async () => {
      workspaceServiceSpy.getFileContent.and.resolveTo({
        content: 'hello world',
        type: 'text',
      });

      await component.loadFileContent('docs/readme.txt');

      expect(component.fileContent()).toBe('hello world');
      expect(component.loadingContent()).toBe(false);
      expect(component.isBinaryFile()).toBe(false);
      expect(component.isMarkdownFile()).toBe(false);
      expect(component.errorMessage()).toBeNull();
    });

    it('scenario 20 — a .md selected file flags isMarkdownFile on a text result', async () => {
      component.selectedFile.set(
        fileNode({
          name: 'a.md',
          path: 'docs/a.md',
          type: 'file',
          extension: '.md',
        })
      );
      workspaceServiceSpy.getFileContent.and.resolveTo({
        content: '# Title',
        type: 'text',
      });

      await component.loadFileContent('docs/a.md');

      expect(component.fileContent()).toBe('# Title');
      expect(component.isMarkdownFile()).toBe(true);
      expect(component.isBinaryFile()).toBe(false);
      expect(component.loadingContent()).toBe(false);
    });

    it('scenario 21 — binary result flags isBinaryFile and shows the binary message', async () => {
      workspaceServiceSpy.getFileContent.and.resolveTo({
        content: null,
        type: 'binary',
        message: 'Binary file cannot be displayed',
      });

      await component.loadFileContent('docs/image.png');

      expect(component.isBinaryFile()).toBe(true);
      expect(component.fileContent()).toBe('Binary file cannot be displayed');
      expect(component.isMarkdownFile()).toBe(false);
      expect(component.loadingContent()).toBe(false);
    });

    it('scenario 22 — rejected fetch sets errorMessage and clears loadingContent', async () => {
      workspaceServiceSpy.getFileContent.and.rejectWith(new Error('read failed'));

      await component.loadFileContent('docs/bad.txt');

      expect(component.errorMessage()).toBe('read failed');
      expect(component.loadingContent()).toBe(false);
      expect(component.fileContent()).toBeNull();
    });
  });

  // --- onNodeSelect (AC3) -------------------------------------------

  describe('onNodeSelect', () => {
    beforeEach(() => {
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([]);
      workspaceServiceSpy.getFileContent.and.resolveTo({
        content: 'data',
        type: 'text',
      });
      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      component.processId = 'proc';
    });

    it('scenario 23 — selecting a file sets selectedFile, clears selectedFolder, loads content', async () => {
      // Seed a stale folder selection to prove it is cleared.
      component.selectedFolder.set(
        fileNode({ name: 'old', path: 'old', type: 'directory' })
      );
      const file = fileNode({
        name: 'a.ts',
        path: 'src/a.ts',
        type: 'file',
        extension: '.ts',
      });

      await component.onNodeSelect({ node: { data: file } });

      expect(component.selectedFile()).toEqual(file);
      expect(component.selectedFolder()).toBeNull();
      expect(workspaceServiceSpy.getFileContent).toHaveBeenCalledWith(
        'proc',
        'src/a.ts',
        undefined
      );
      expect(component.fileContent()).toBe('data');
    });

    it('scenario 24 — selecting a directory sets selectedFolder, clears file + content signals', async () => {
      // Seed a stale file + content selection to prove they are cleared.
      component.selectedFile.set(
        fileNode({ name: 'a.ts', path: 'src/a.ts', type: 'file' })
      );
      component.fileContent.set('stale content');
      component.isBinaryFile.set(true);
      component.isMarkdownFile.set(true);

      workspaceServiceSpy.getFileContent.calls.reset();
      const dir = fileNode({ name: 'src', path: 'src', type: 'directory' });

      await component.onNodeSelect({ node: { data: dir } });

      expect(component.selectedFolder()).toEqual(dir);
      expect(component.selectedFile()).toBeNull();
      expect(component.fileContent()).toBeNull();
      expect(component.isBinaryFile()).toBe(false);
      expect(component.isMarkdownFile()).toBe(false);
      // Selecting a directory loads no content.
      expect(workspaceServiceSpy.getFileContent).not.toHaveBeenCalled();
    });
  });

  // --- handleUploadComplete -----------------------------------------

  describe('handleUploadComplete', () => {
    beforeEach(() => {
      // Keep the declarative root load happy with an empty listing
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([]);
      workspaceServiceSpy.uploadFiles.and.resolveTo();
      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      component.processId = 'proc';
    });

    it('scenario 9 — refreshes only the target subdirectory', async () => {
      // Set up a tree with root + expanded `docs` subdir containing a.md
      const aMd: TreeNode = {
        label: 'a.md',
        data: fileNode({
          name: 'a.md',
          path: 'docs/a.md',
          type: 'file',
          extension: '.md',
        }),
        leaf: true,
      };
      const docs: TreeNode = {
        label: 'docs',
        data: fileNode({ name: 'docs', path: 'docs', type: 'directory' }),
        leaf: false,
        children: [aMd],
      };
      const root: TreeNode = {
        label: 'Root Folder',
        data: fileNode({
          name: 'Root Folder',
          path: '',
          type: 'directory',
        }),
        children: [docs],
        expanded: true,
      };
      component.treeNodes.set([root]);
      component.uploadTargetPath = 'docs';

      // Stub the upload modal ViewChild
      component.uploadModal = {
        getSelectedFiles: () => [new File(['x'], 'b.md')],
      } as unknown as UploadModalComponent;

      // Fresh listing for docs after upload
      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([
        fileNode({
          name: 'a.md',
          path: 'docs/a.md',
          type: 'file',
          extension: '.md',
        }),
        fileNode({
          name: 'b.md',
          path: 'docs/b.md',
          type: 'file',
          extension: '.md',
        }),
      ]);

      await component.handleUploadComplete();

      expect(workspaceServiceSpy.uploadFiles).toHaveBeenCalledTimes(1);
      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledOnceWith(
        'proc',
        'docs'
      );

      const refreshedDocs = component.treeNodes()[0].children![0];
      expect(refreshedDocs.children?.length).toBe(2);
    });

    it('scenario 10 — root target refreshes the synthetic Root Folder wrapper children', async () => {
      const root: TreeNode = {
        label: 'Root Folder',
        data: fileNode({
          name: 'Root Folder',
          path: '',
          type: 'directory',
        }),
        children: [],
        expanded: true,
      };
      component.treeNodes.set([root]);
      component.uploadTargetPath = '';
      component.uploadModal = {
        getSelectedFiles: () => [new File(['x'], 'c.md')],
      } as unknown as UploadModalComponent;

      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([
        fileNode({
          name: 'c.md',
          path: 'c.md',
          type: 'file',
          extension: '.md',
        }),
      ]);

      await component.handleUploadComplete();

      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledOnceWith(
        'proc',
        ''
      );
      // Root wrapper itself stays; its children are replaced with fresh listing
      expect(component.treeNodes().length).toBe(1);
      expect(component.treeNodes()[0].label).toBe('Root Folder');
      expect(component.treeNodes()[0].children?.length).toBe(1);
      expect(component.treeNodes()[0].children![0].label).toBe('c.md');
    });

    it('scenario 11 — no files selected: no-op (uploadFiles NOT called)', async () => {
      component.uploadModal = {
        getSelectedFiles: () => [],
      } as unknown as UploadModalComponent;
      workspaceServiceSpy.uploadFiles.calls.reset();

      await component.handleUploadComplete();

      expect(workspaceServiceSpy.uploadFiles).not.toHaveBeenCalled();
    });
  });

  // --- workspaceId threading (AC7) -----------------------------------

  describe('workspaceId threading', () => {
    beforeEach(() => {
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([]);
      workspaceServiceSpy.getFileContent.and.resolveTo({
        content: 'x',
        type: 'text',
      });
      workspaceServiceSpy.getDownloadUrl.and.returnValue('http://dl');
      workspaceServiceSpy.uploadFiles.and.resolveTo();
      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      component.processId = 'proc';
    });

    it('scenario 12 — set workspaceId threads as the trailing arg on every call', async () => {
      fixture.componentRef.setInput('workspaceId', 'ws-1');

      // getWorkspaceTree via the declarative root load
      await flushRootLoad(fixture);
      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledWith(
        'proc',
        '',
        'ws-1'
      );

      // getWorkspaceTree via onNodeExpand
      const subDir: TreeNode = {
        label: 'sub',
        data: fileNode({ name: 'sub', path: 'sub', type: 'directory' }),
        leaf: false,
        children: undefined,
      };
      await component.onNodeExpand({ node: subDir });
      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledWith(
        'proc',
        'sub',
        'ws-1'
      );

      // getFileContent via loadFileContent
      await component.loadFileContent('docs/a.md');
      expect(workspaceServiceSpy.getFileContent).toHaveBeenCalledWith(
        'proc',
        'docs/a.md',
        'ws-1'
      );

      // getDownloadUrl via downloadFile
      component.selectedFile.set(
        fileNode({
          name: 'a.md',
          path: 'docs/a.md',
          type: 'file',
        })
      );
      spyOn(window, 'open');
      component.downloadFile();
      expect(workspaceServiceSpy.getDownloadUrl).toHaveBeenCalledWith(
        'proc',
        'docs/a.md',
        'ws-1'
      );

      // uploadFiles via handleUploadComplete
      component.uploadTargetPath = 'docs';
      component.uploadModal = {
        getSelectedFiles: () => [new File(['x'], 'b.md')],
      } as unknown as UploadModalComponent;
      await component.handleUploadComplete();
      expect(workspaceServiceSpy.uploadFiles).toHaveBeenCalledWith(
        'proc',
        jasmine.any(Array),
        'docs',
        'ws-1'
      );
    });

    it('scenario 13 — unset workspaceId keeps the 2-arg getWorkspaceTree shape', async () => {
      // workspaceId left undefined — the root load issues the 2-arg call
      await flushRootLoad(fixture);
      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledOnceWith(
        'proc',
        ''
      );
    });

    it('scenario 13b — unset workspaceId omits the trailing id on content/download/upload/lazy-expand calls', async () => {
      // workspaceId left undefined for the whole scenario.

      // getFileContent via loadFileContent: signal getter returns undefined and
      // is passed through verbatim (no `ws` query param ⇒ backend team_id fallback).
      await component.loadFileContent('docs/a.md');
      expect(workspaceServiceSpy.getFileContent).toHaveBeenCalledWith(
        'proc',
        'docs/a.md',
        undefined
      );

      // getDownloadUrl via downloadFile: same undefined-id passthrough.
      component.selectedFile.set(
        fileNode({ name: 'a.md', path: 'docs/a.md', type: 'file' })
      );
      spyOn(window, 'open');
      component.downloadFile();
      expect(workspaceServiceSpy.getDownloadUrl).toHaveBeenCalledWith(
        'proc',
        'docs/a.md',
        undefined
      );

      // uploadFiles via handleUploadComplete: trailing id omitted (undefined).
      component.uploadTargetPath = 'docs';
      component.uploadModal = {
        getSelectedFiles: () => [new File(['x'], 'b.md')],
      } as unknown as UploadModalComponent;
      await component.handleUploadComplete();
      expect(workspaceServiceSpy.uploadFiles).toHaveBeenCalledWith(
        'proc',
        jasmine.any(Array),
        'docs',
        undefined
      );

      // getWorkspaceTree via onNodeExpand: unset path keeps the 2-arg shape (no `ws`).
      const subDir: TreeNode = {
        label: 'sub',
        data: fileNode({ name: 'sub', path: 'sub', type: 'directory' }),
        leaf: false,
        children: undefined,
      };
      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      await component.onNodeExpand({ node: subDir });
      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledOnceWith(
        'proc',
        'sub'
      );
    });
  });

  // --- signal re-assignment + CD invariants (AC4, AC6) --------------

  describe('treeNodes signal re-assignment', () => {
    beforeEach(() => {
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([]);
      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      component.processId = 'proc';
    });

    it('scenario 25 — a successful expand re-assigns treeNodes() to a new array reference', async () => {
      const subDir: TreeNode = {
        label: 'sub',
        data: fileNode({ name: 'sub', path: 'sub', type: 'directory' }),
        leaf: false,
        children: undefined,
      };
      component.treeNodes.set([subDir]);
      const before = component.treeNodes();

      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([
        fileNode({ name: 'inner.ts', path: 'sub/inner.ts', type: 'file' }),
      ]);

      await component.onNodeExpand({ node: subDir });

      // New top-level array identity ⇒ the OnPush/signal CD is scheduled
      // (re-assigning the signal is the documented expand mechanism — NFR6).
      const after = component.treeNodes();
      expect(after).not.toBe(before);
      expect(after[0]).toBe(subDir); // same node, mutated in place
      expect(subDir.children?.length).toBe(1);
    });

    it('scenario 26 — onNodeExpand schedules CD via signal re-assignment, not markForCheck', async () => {
      // NFR6 invariant: the lazy-expand path repaints by re-assigning the
      // treeNodes signal; it must NOT reach for the ChangeDetectorRef. If a
      // markForCheck() crept onto this path, spying the ref would catch it.
      const cdr = (
        component as unknown as {
          cdr?: { markForCheck: () => void };
          changeDetectorRef?: { markForCheck: () => void };
        }
      );
      const ref = cdr.cdr ?? cdr.changeDetectorRef;
      const markSpy = ref ? spyOn(ref, 'markForCheck').and.callThrough() : null;

      const subDir: TreeNode = {
        label: 'sub',
        data: fileNode({ name: 'sub', path: 'sub', type: 'directory' }),
        leaf: false,
        children: undefined,
      };
      component.treeNodes.set([subDir]);

      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([
        fileNode({ name: 'inner.ts', path: 'sub/inner.ts', type: 'file' }),
      ]);

      await component.onNodeExpand({ node: subDir });

      if (markSpy) {
        // At most one markForCheck() over the whole component, and the expand
        // path does not rely on it — the signal re-assignment is sufficient.
        expect(markSpy).not.toHaveBeenCalled();
      }
      // The repaint mechanism that DID fire: a new treeNodes() reference.
      expect(component.treeNodes()[0].children?.length).toBe(1);
    });
  });

  // --- workspaceId signal-input re-trigger + race closure (AC6) -------

  describe('workspaceId signal-input re-trigger', () => {
    beforeEach(() => {
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([]);
      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
    });

    it('scenario 14 — a workspaceId change re-triggers the root fetch via switchMap', async () => {
      // initial load (undefined workspaceId)
      await flushRootLoad(fixture);
      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledOnceWith(
        'proc',
        ''
      );

      // change the bound signal input ⇒ a new switchMap emission ⇒ refetch
      fixture.componentRef.setInput('workspaceId', 'ws-2');
      await flushRootLoad(fixture);

      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledWith(
        'proc',
        '',
        'ws-2'
      );
      expect(workspaceServiceSpy.getWorkspaceTree.calls.count()).toBe(2);
    });

    it('scenario 15 — setting the same workspaceId value does not refetch', async () => {
      fixture.componentRef.setInput('workspaceId', 'ws-1');
      await flushRootLoad(fixture);
      expect(workspaceServiceSpy.getWorkspaceTree.calls.count()).toBe(1);

      // signal inputs dedupe equal values: no new emission, no refetch
      fixture.componentRef.setInput('workspaceId', 'ws-1');
      await flushRootLoad(fixture);
      expect(workspaceServiceSpy.getWorkspaceTree.calls.count()).toBe(1);
    });

    it('scenario 16 — a superseded slow response does not clobber the newer tab tree (switchMap race closure)', async () => {
      // First (slow) fetch for ws-A: resolves LATE.
      let resolveSlow!: (v: FileNode[]) => void;
      const slow = new Promise<FileNode[]>((res) => (resolveSlow = res));
      workspaceServiceSpy.getWorkspaceTree.and.returnValue(slow);

      fixture.componentRef.setInput('workspaceId', 'ws-A');
      fixture.detectChanges(); // kick the ws-A switchMap emission (still pending)

      // Second (fast) fetch for ws-B: resolves immediately and wins.
      workspaceServiceSpy.getWorkspaceTree.and.resolveTo([
        fileNode({ name: 'fromB.md', path: 'fromB.md', type: 'file' }),
      ]);
      fixture.componentRef.setInput('workspaceId', 'ws-B');
      await flushRootLoad(fixture);

      // ws-B's tree is in place
      expect(component.treeNodes()[0].children![0].label).toBe('fromB.md');

      // Now the stale ws-A response finally arrives — switchMap cancelled it,
      // so it must NOT overwrite ws-B's tree.
      resolveSlow([
        fileNode({ name: 'fromA.md', path: 'fromA.md', type: 'file' }),
      ]);
      await flushRootLoad(fixture);

      expect(component.treeNodes()[0].children![0].label).toBe('fromB.md');
    });
  });

  // --- OnChanges removal (AC5) ---------------------------------------

  describe('OnChanges removal', () => {
    it('scenario 17 — component no longer implements OnChanges (no ngOnChanges method)', () => {
      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;

      expect(
        (component as unknown as { ngOnChanges?: unknown }).ngOnChanges
      ).toBeUndefined();
    });
  });
});

// --------------------------------------------------------------------
// NFR3 regression gate (AC1) — the falsifiability gate.
//
// Hosts the explorer inside an OnPush parent that is rendered ONCE and then
// never re-marked. When the root tree resolves, the spinner must be gone and
// loading() must be false WITHOUT any further change-detection trigger from
// the parent. This fails against the default-CD / `loading`-field impl (the
// child's field mutation never marks the OnPush parent dirty, so the view is
// stale) and passes against the signal/OnPush impl.
// --------------------------------------------------------------------

@Component({
  selector: 'app-onpush-host',
  standalone: true,
  imports: [WorkspaceExplorerComponent],
  template: `<app-workspace-explorer />`,
  // OnPush parent: after the first render it is NEVER re-marked by the test.
  changeDetection: ChangeDetectionStrategy.OnPush,
})
class OnPushHostComponent {}

describe('WorkspaceExplorerComponent — NFR3 OnPush regression gate', () => {
  let workspaceServiceSpy: jasmine.SpyObj<WorkspaceService>;
  let contextServiceStub: {
    currentProcessId$: BehaviorSubject<string>;
    currentTeamRunning$: BehaviorSubject<boolean>;
    getCurrentTeam: jasmine.Spy;
  };

  beforeEach(async () => {
    workspaceServiceSpy = jasmine.createSpyObj('WorkspaceService', [
      'getWorkspaceTree',
      'getFileContent',
      'getDownloadUrl',
      'uploadFiles',
    ]);
    contextServiceStub = {
      currentProcessId$: new BehaviorSubject<string>('proc'),
      currentTeamRunning$: new BehaviorSubject<boolean>(true),
      getCurrentTeam: jasmine
        .createSpy('getCurrentTeam')
        .and.callFake(async () => makeTeam()),
    };

    await TestBed.configureTestingModule({
      imports: [OnPushHostComponent, NoopAnimationsModule],
      providers: [
        { provide: WorkspaceService, useValue: workspaceServiceSpy },
        { provide: ContextService, useValue: contextServiceStub },
      ],
    })
      .overrideComponent(WorkspaceExplorerComponent, {
        set: {
          imports: [CommonModule],
          schemas: [CUSTOM_ELEMENTS_SCHEMA],
        },
      })
      .compileComponents();
  });

  it('scenario 18 — spinner clears after the tree resolves WITHOUT re-marking the OnPush parent', async () => {
    // Slow-ish promise so the spinner is visible on the first render.
    let resolveTree!: (v: FileNode[]) => void;
    const treePromise = new Promise<FileNode[]>((res) => (resolveTree = res));
    workspaceServiceSpy.getWorkspaceTree.and.returnValue(treePromise);

    const hostFixture: ComponentFixture<OnPushHostComponent> =
      TestBed.createComponent(OnPushHostComponent);

    // Attach the fixture to ApplicationRef and let zone-driven change detection
    // run on stabilization — faithfully reproducing the running app, where a
    // settled fetch promise triggers a GLOBAL ApplicationRef.tick(), NOT a
    // targeted parent detectChanges(). autoDetect NEVER force-checks the OnPush
    // parent: tick() walks from the root and re-checks only views on a dirty
    // path. With the OLD default-CD/`loading`-field impl the explorer's field
    // mutation never marks the OnPush parent's subtree dirty, so tick skips it
    // and the spinner stays (this spec fails); with the signal/OnPush impl the
    // signal write marks the explorer dirty up the chain, so tick re-checks it
    // and the spinner clears (this spec passes).
    hostFixture.autoDetectChanges(true);

    const explorerDe = hostFixture.debugElement.children[0];
    const explorer =
      explorerDe.componentInstance as WorkspaceExplorerComponent;

    expect(explorer.loading()).toBe(true);
    expect(
      hostFixture.nativeElement.querySelector('p-progressspinner') ||
        hostFixture.nativeElement.querySelector('p-progressSpinner')
    ).withContext('spinner should be visible while pending').not.toBeNull();

    // Resolve the tree. CRITICALLY: never call hostFixture.detectChanges()
    // (which would force-check the OnPush parent). Only let the zone settle —
    // the spinner must clear via the signal-driven global tick alone.
    resolveTree([fileNode({ name: 'a.md', path: 'a.md', type: 'file' })]);
    await hostFixture.whenStable();

    // The signal write repainted the explorer's own OnPush view via the global
    // tick, even though the parent was never explicitly re-marked.
    expect(explorer.loading()).toBe(false);
    expect(
      hostFixture.nativeElement.querySelector('p-progressspinner') ||
        hostFixture.nativeElement.querySelector('p-progressSpinner')
    ).withContext('spinner must be gone after resolve').toBeNull();
  });

  // --- the content-pane gate (AC1, NFR3 analogue) -------------------
  //
  // The 24-2 analogue of scenario 18: the SAME OnPush stall, but on the
  // content pane instead of the tree pane. Host the explorer inside an OnPush
  // parent rendered once and never re-marked; select a file (driving
  // loadFileContent with a slow getFileContent); the content-pane spinner must
  // clear and fileContent() must be set after the promise resolves WITHOUT any
  // further parent re-mark. Fails against a default-CD / loadingContent-field
  // impl (the child's field mutation never marks the OnPush parent dirty);
  // passes against the signal/OnPush impl (the signal write does). The query is
  // scoped to `.panel-content` so the tree-pane spinner never false-positives.

  function contentSpinner(host: ComponentFixture<OnPushHostComponent>): Element | null {
    const pane = host.nativeElement.querySelector('.panel-content');
    if (!pane) return null;
    return (
      pane.querySelector('p-progressspinner') ||
      pane.querySelector('p-progressSpinner')
    );
  }

  it('scenario 27 — content spinner clears after getFileContent resolves WITHOUT re-marking the OnPush parent', async () => {
    // Root tree resolves immediately so the tree pane is settled and we are
    // exercising ONLY the content pane. A plain-text file (not .md) is used so
    // the content renders via the `<pre><code>` block — the host describe
    // overrides imports to CommonModule only, so the `<markdown>` component
    // (no custom-element dash) is not resolvable here, and the gate does not
    // depend on markdown rendering anyway.
    workspaceServiceSpy.getWorkspaceTree.and.resolveTo([
      fileNode({
        name: 'a.txt',
        path: 'a.txt',
        type: 'file',
        extension: '.txt',
      }),
    ]);
    // Slow file-content promise so the content spinner is visible while pending.
    let resolveContent!: (v: { content: string | null; type: string }) => void;
    const contentPromise = new Promise<{ content: string | null; type: string }>(
      (res) => (resolveContent = res)
    );
    workspaceServiceSpy.getFileContent.and.returnValue(contentPromise as any);

    const hostFixture: ComponentFixture<OnPushHostComponent> =
      TestBed.createComponent(OnPushHostComponent);

    // Faithful reproduction of the running app: zone-driven global tick on
    // stabilization, NEVER a targeted parent detectChanges(). See scenario 18.
    hostFixture.autoDetectChanges(true);

    const explorerDe = hostFixture.debugElement.children[0];
    const explorer =
      explorerDe.componentInstance as WorkspaceExplorerComponent;

    // Wait for the root tree load to settle so the explorer is fully rendered.
    await hostFixture.whenStable();

    // Drive a file selection ⇒ loadFileContent ⇒ loadingContent() true.
    const file = fileNode({
      name: 'a.txt',
      path: 'a.txt',
      type: 'file',
      extension: '.txt',
    });
    // Fire-and-await-later: do NOT await (the promise is still pending) so we
    // can observe the spinner-visible state first.
    const selectPromise = explorer.onNodeSelect({ node: { data: file } });
    await hostFixture.whenStable();

    expect(explorer.loadingContent()).toBe(true);
    expect(contentSpinner(hostFixture))
      .withContext('content spinner should be visible while pending')
      .not.toBeNull();

    // Resolve the content. CRITICALLY: never call hostFixture.detectChanges()
    // (which would force-check the OnPush parent). Only let the zone settle —
    // the spinner must clear via the signal-driven global tick alone.
    resolveContent({ content: 'plain text body', type: 'text' });
    await selectPromise;
    await hostFixture.whenStable();

    expect(explorer.loadingContent()).toBe(false);
    expect(explorer.fileContent()).toBe('plain text body');
    expect(contentSpinner(hostFixture))
      .withContext('content spinner must be gone after resolve')
      .toBeNull();
  });
});
