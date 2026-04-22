import { CommonModule } from '@angular/common';
import { CUSTOM_ELEMENTS_SCHEMA } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { TreeNode } from 'primeng/api';
import { BehaviorSubject } from 'rxjs';

import { ContextService } from '../../services/context.service';
import { FileNode, WorkspaceService } from '../../services/workspace.service';
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

  // --- loadWorkspace -------------------------------------------------

  describe('loadWorkspace', () => {
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
      // Don't detectChanges yet — call loadWorkspace explicitly and await it
      await component.ngOnInit();

      expect(component.treeNodes.length).toBe(1);
      const root = component.treeNodes[0];
      expect(root.label).toBe('Root Folder');
      expect(root.children?.length).toBe(2);

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
      await component.ngOnInit();

      // Synthetic root always exists; its children are []
      expect(component.treeNodes.length).toBe(1);
      expect(component.treeNodes[0].label).toBe('Root Folder');
      expect(component.treeNodes[0].children).toEqual([]);
      expect(component.errorMessage).toBeNull();
    });

    it('scenario 3 — HTTP error sets errorMessage and clears loading', async () => {
      workspaceServiceSpy.getWorkspaceTree.and.rejectWith(new Error('500'));

      fixture = TestBed.createComponent(WorkspaceExplorerComponent);
      component = fixture.componentInstance;
      await component.ngOnInit();

      expect(component.errorMessage).toBe('500');
      expect(component.loading).toBe(false);
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
      component.treeNodes = [subDir];

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
      component.treeNodes = [subDir];

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
      component.treeNodes = [subDir];

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
      component.treeNodes = [subDir];

      workspaceServiceSpy.getWorkspaceTree.calls.reset();
      workspaceServiceSpy.getWorkspaceTree.and.rejectWith(new Error('boom'));

      await component.onNodeExpand({ node: subDir });

      expect(component.errorMessage).toBe('boom');
      expect(subDir.children).toBeUndefined();
    });
  });

  // --- handleUploadComplete -----------------------------------------

  describe('handleUploadComplete', () => {
    beforeEach(() => {
      // Keep ngOnInit's loadWorkspace call happy with an empty listing
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
      component.treeNodes = [root];
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

      const loadWorkspaceSpy = spyOn(component, 'loadWorkspace').and.callThrough();

      await component.handleUploadComplete();

      expect(workspaceServiceSpy.uploadFiles).toHaveBeenCalledTimes(1);
      expect(workspaceServiceSpy.getWorkspaceTree).toHaveBeenCalledOnceWith(
        'proc',
        'docs'
      );

      const refreshedDocs = component.treeNodes[0].children![0];
      expect(refreshedDocs.children?.length).toBe(2);

      // Critically: no full-tree refresh was issued
      expect(loadWorkspaceSpy).not.toHaveBeenCalled();
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
      component.treeNodes = [root];
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
      expect(component.treeNodes.length).toBe(1);
      expect(component.treeNodes[0].label).toBe('Root Folder');
      expect(component.treeNodes[0].children?.length).toBe(1);
      expect(component.treeNodes[0].children![0].label).toBe('c.md');
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
});
