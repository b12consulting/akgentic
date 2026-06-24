import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  OnInit,
  inject,
  input,
  signal,
  ViewChild,
} from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import { TreeModule } from 'primeng/tree';
import { TreeNode } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { ProgressSpinnerModule } from 'primeng/progressspinner';
import { ScrollPanelModule } from 'primeng/scrollpanel';
import { TooltipModule } from 'primeng/tooltip';
import { CardModule } from 'primeng/card';
import { ToolbarModule } from 'primeng/toolbar';
import { DividerModule } from 'primeng/divider';
import { TagModule } from 'primeng/tag';
import { MarkdownModule } from 'ngx-markdown';
import { firstValueFrom, from, of, switchMap } from 'rxjs';
import { catchError, map, tap } from 'rxjs/operators';
import {
  WorkspaceService,
  FileNode,
  FileContent,
} from '../../workspace/workspace.service';
import { ContextService } from '../../../../core/context/context.service';
import { UploadModalComponent } from './upload-modal/upload-modal.component';

/**
 * Outcome of one declarative root-tree load. `switchMap` maps each
 * `workspaceId` emission to a stream of these so the subscriber only ever
 * applies the LATEST load's result — a superseded slow response is cancelled
 * before it can clobber a newer tab's tree (ADR-021 §Decision 2, race closure).
 */
interface RootLoadResult {
  nodes: TreeNode[] | null;
  error: string | null;
}

@Component({
  selector: 'app-workspace-explorer',
  standalone: true,
  imports: [
    CommonModule,
    TreeModule,
    ButtonModule,
    ProgressSpinnerModule,
    ScrollPanelModule,
    TooltipModule,
    CardModule,
    ToolbarModule,
    DividerModule,
    TagModule,
    MarkdownModule,
    UploadModalComponent,
  ],
  templateUrl: './workspace-explorer.component.html',
  styleUrls: ['./workspace-explorer.component.scss'],
  // OnPush + signals: a signal write notifies the OnPush chain automatically,
  // so the explorer's subtree is no longer skipped when ApplicationRef.tick
  // walks past an OnPush ancestor that the child never marked dirty (ADR-021
  // §Decision 1 — this is what removes the multi-second spinner stall).
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class WorkspaceExplorerComponent implements OnInit {
  @ViewChild(UploadModalComponent) uploadModal!: UploadModalComponent;

  /**
   * Optional workspace addressed by this explorer (Epic 23 / ADR-019). A signal
   * input with an `undefined` default behaves byte-for-byte like the previous
   * optional `@Input`: unset ⇒ every WorkspaceService call omits `workspaceId`
   * so the backend falls back to `team_id`; set ⇒ it is threaded through every
   * call. A change re-triggers the root tree load via `toObservable → switchMap`
   * (ADR-021 §Decision 4 — contract preserved).
   */
  workspaceId = input<string | undefined>();

  workspaceService = inject(WorkspaceService);
  contextService = inject(ContextService);
  private destroyRef = inject(DestroyRef);

  processId: string = '';
  isProcessRunning: boolean = false;

  // Template-bound state as signals — signal writes notify the OnPush chain.
  treeNodes = signal<TreeNode[]>([]);
  selectedFile = signal<FileNode | null>(null);
  selectedFolder = signal<FileNode | null>(null);
  fileContent = signal<string | null>(null);
  loading = signal(false);
  loadingContent = signal(false);
  isBinaryFile = signal(false);
  isMarkdownFile = signal(false);
  errorMessage = signal<string | null>(null);

  // Plain fields: not template-bound through *ngIf/[value] in a way that the
  // OnPush stall affects (sidebar/upload-modal toggles are driven by user
  // events that already mark the view), so they stay as ordinary fields.
  sidebarVisible = false; // Start collapsed
  uploadModalVisible = false;
  uploadTargetPath: string = '';

  constructor() {
    // Resolve processId synchronously from the BehaviorSubject so it is
    // available at the root stream's first emission (ngOnInit's await would
    // otherwise race the toObservable(workspaceId) first tick).
    this.processId = this.contextService.currentProcessId$.value;

    // Declarative root-tree load: every workspaceId emission (incl. the initial
    // `undefined`) maps to a fresh fetch; switchMap cancels the in-flight
    // previous load so a superseded slow response cannot overwrite a newer
    // tab's treeNodes (ADR-021 §Decision 2). Stable APIs only — no resource().
    toObservable(this.workspaceId)
      .pipe(
        switchMap((ws) => this.loadRootTree$(ws)),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((result) => this.applyRootLoad(result));
  }

  async ngOnInit() {
    await this.checkProcessStatus();
  }

  /**
   * Map one `workspaceId` value to a root-load stream that drives the loading
   * spinner on subscribe, resolves to the synthetic Root Folder wrapper on
   * success, and maps a rejection to an error message. Returns an empty
   * (no-op) load when `processId` is not yet resolved.
   */
  private loadRootTree$(ws?: string) {
    if (!this.processId) {
      return of<RootLoadResult>({ nodes: null, error: null });
    }

    this.loading.set(true);
    this.errorMessage.set(null);

    return from(this.fetchTree('', ws)).pipe(
      map((tree): RootLoadResult => ({ nodes: this.wrapRootTree(tree), error: null })),
      catchError((error: any) => {
        console.error('Error loading workspace', error);
        return of<RootLoadResult>({
          nodes: null,
          error: error?.message || 'Failed to load workspace',
        });
      }),
      tap(() => this.loading.set(false)),
    );
  }

  /** Apply the latest (switchMap-guarded) root-load result to the signals. */
  private applyRootLoad(result: RootLoadResult): void {
    if (result.error !== null) {
      this.errorMessage.set(result.error);
      return;
    }
    if (result.nodes !== null) {
      this.treeNodes.set(result.nodes);
    }
  }

  /** Wrap converted backend entries in the synthetic Root Folder node. */
  private wrapRootTree(tree: FileNode[]): TreeNode[] {
    const rootNode: TreeNode = {
      label: 'Root Folder',
      data: { name: 'Root Folder', path: '', type: 'directory' } as FileNode,
      icon: 'pi pi-home',
      children: this.convertToTreeNodes(tree),
      expanded: true,
      selectable: true,
    };
    return [rootNode];
  }

  /**
   * Fetch a directory listing, threading `workspaceId` only when set so the
   * unset path keeps today's 2-arg call shape (and byte-identical URL). The
   * id is passed explicitly (rather than read off the signal) so the root load
   * uses the value that drove the current switchMap emission.
   */
  private fetchTree(path: string, ws: string | undefined = this.workspaceId()): Promise<FileNode[]> {
    return ws
      ? this.workspaceService.getWorkspaceTree(this.processId, path, ws)
      : this.workspaceService.getWorkspaceTree(this.processId, path);
  }

  async checkProcessStatus() {
    this.isProcessRunning = await firstValueFrom(
      this.contextService.currentTeamRunning$,
    );
  }

  convertToTreeNodes(nodes: FileNode[]): TreeNode[] {
    return nodes.map((node) => ({
      label: node.name,
      data: node,
      icon: this.getFileIcon(node),
      children: node.children
        ? this.convertToTreeNodes(node.children)
        : undefined,
      leaf: node.type === 'file',
      expanded: false,
    }));
  }

  getFileIcon(node: FileNode): string {
    if (node.type === 'directory') {
      return 'pi pi-folder';
    }

    const ext = node.extension?.toLowerCase() || '';
    const iconMap: { [key: string]: string } = {
      '.py': 'pi pi-file',
      '.ts': 'pi pi-file',
      '.js': 'pi pi-file',
      '.html': 'pi pi-file',
      '.css': 'pi pi-file',
      '.scss': 'pi pi-file',
      '.json': 'pi pi-file',
      '.md': 'pi pi-file',
      '.txt': 'pi pi-file',
      '.yml': 'pi pi-file',
      '.yaml': 'pi pi-file',
      '.xml': 'pi pi-file',
      '.sql': 'pi pi-file',
      '.sh': 'pi pi-file',
      '.java': 'pi pi-file',
      '.cpp': 'pi pi-file',
      '.c': 'pi pi-file',
      '.h': 'pi pi-file',
      '.go': 'pi pi-file',
      '.rs': 'pi pi-file',
      '.php': 'pi pi-file',
      '.rb': 'pi pi-file',
    };

    return iconMap[ext] || 'pi pi-file';
  }

  /**
   * PrimeNG lazy-expand handler: when a user clicks the expand arrow on a
   * directory TreeNode whose children have never been fetched (`children ===
   * undefined`), fetch that directory's entries from `WorkspaceService` and
   * splice them into the node. Loaded-empty (`children === []`) and loaded-
   * populated directories short-circuit via the `!== undefined` guard — the
   * second expand on any directory never issues a second HTTP call.
   */
  async onNodeExpand(event: {
    node: TreeNode;
    originalEvent?: Event;
  }): Promise<void> {
    const node = event.node;
    const fileNode = node.data as FileNode | undefined;

    // Only directories are lazy-loaded
    if (!fileNode || fileNode.type !== 'directory') return;
    // Cache hit: already loaded (empty or populated)
    if (node.children !== undefined) return;

    try {
      const children = await this.fetchTree(fileNode.path);
      node.children = this.convertToTreeNodes(children);
      // Re-assign the top-level array reference so the bound treeNodes signal
      // picks up the mutation on a nested TreeNode's `children` property.
      this.treeNodes.set([...this.treeNodes()]);
    } catch (error: any) {
      console.error('Error loading subdirectory', error);
      this.errorMessage.set(error?.message || 'Failed to load subdirectory');
      // Leave node.children as undefined so a subsequent user-initiated
      // expand can retry the fetch.
    }
  }

  async onNodeSelect(event: any) {
    const node: FileNode = event.node.data;

    if (node.type === 'file') {
      this.selectedFile.set(node);
      this.selectedFolder.set(null);
      await this.loadFileContent(node.path);
    } else if (node.type === 'directory') {
      this.selectedFile.set(null);
      this.selectedFolder.set(node);
      this.fileContent.set(null);
      this.isBinaryFile.set(false);
      this.isMarkdownFile.set(false);
    }
  }

  async loadFileContent(path: string) {
    this.loadingContent.set(true);
    this.fileContent.set(null);
    this.isBinaryFile.set(false);
    this.isMarkdownFile.set(false);
    this.errorMessage.set(null);

    try {
      const result: FileContent = await this.workspaceService.getFileContent(
        this.processId,
        path,
        this.workspaceId()
      );

      if (result.type === 'binary') {
        this.isBinaryFile.set(true);
        this.fileContent.set(result.message || 'Binary file cannot be displayed');
      } else {
        this.fileContent.set(result.content);
        // Check if file is markdown
        if (this.selectedFile()?.extension?.toLowerCase() === '.md') {
          this.isMarkdownFile.set(true);
        }
      }
    } catch (error: any) {
      console.error('Error loading file content', error);
      this.errorMessage.set(error?.message || 'Failed to load file content');
    } finally {
      this.loadingContent.set(false);
    }
  }

  downloadFile() {
    const selected = this.selectedFile();
    if (!selected) return;

    const url = this.workspaceService.getDownloadUrl(
      this.processId,
      selected.path,
      this.workspaceId()
    );
    window.open(url, '_blank');
  }

  formatFileSize(bytes: number | undefined): string {
    if (!bytes) return '0 B';

    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));

    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
  }

  refresh() {
    // Re-run the root load for the current workspaceId via the declarative
    // stream (the only owner of the loading/treeNodes lifecycle now).
    this.loadRootTree$(this.workspaceId())
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((result) => this.applyRootLoad(result));
  }

  toggleSidebar() {
    this.sidebarVisible = !this.sidebarVisible;
  }

  onNavigatorHover() {
    // Only expand on hover if collapsed
    if (!this.sidebarVisible) {
      this.sidebarVisible = true;
    }
  }

  onNavigatorLeave() {
    // Auto-collapse when mouse leaves the panel
    this.sidebarVisible = false;
  }

  openUploadModal(targetPath?: string) {
    this.uploadTargetPath = targetPath || '';
    this.uploadModalVisible = true;
  }

  openUploadModalToCurrentSelection() {
    const folder = this.selectedFolder();
    const file = this.selectedFile();
    // If a folder is selected, upload there
    if (folder) {
      this.openUploadModal(folder.path);
    }
    // If a file is selected, upload to its parent folder
    else if (file) {
      const parentPath = this.getParentPath(file.path);
      this.openUploadModal(parentPath);
    }
    // Otherwise upload to root
    else {
      this.openUploadModal();
    }
  }

  private getParentPath(filePath: string): string {
    if (!filePath) return '';
    const parts = filePath.split('/');
    parts.pop(); // Remove the file name
    return parts.join('/');
  }

  async handleUploadComplete() {
    // Get selected files from the modal
    const files = this.uploadModal.getSelectedFiles();

    if (files.length === 0) {
      return;
    }

    try {
      await this.workspaceService.uploadFiles(
        this.processId,
        files,
        this.uploadTargetPath,
        this.workspaceId()
      );

      // Refresh ONLY the directory the user uploaded to — preserves the
      // rest of the user's expansion state. Root ('') targets the synthetic
      // Root Folder wrapper; subdirs are located via `findTreeNodeByPath`.
      await this.refreshDirectory(this.uploadTargetPath);
    } catch (error: any) {
      console.error('Upload failed', error);
      throw error;
    }
  }

  /**
   * Re-fetch a single directory listing and splice the fresh children into
   * the tree at that path. For the root ('') this replaces the synthetic
   * Root Folder wrapper's children (the wrapper itself stays). For subdirs
   * we walk the tree to locate the matching TreeNode; if not found (e.g.
   * user uploaded to a dir that hasn't been expanded yet), silently return —
   * the next manual expand will lazy-fetch the fresh listing anyway.
   */
  private async refreshDirectory(path: string): Promise<void> {
    const fresh = await this.fetchTree(path);
    const freshNodes = this.convertToTreeNodes(fresh);

    if (path === '') {
      const current = this.treeNodes();
      if (current.length > 0) {
        current[0].children = freshNodes;
        this.treeNodes.set([...current]);
      }
      return;
    }

    const current = this.treeNodes();
    const target = this.findTreeNodeByPath(current, path);
    if (target) {
      target.children = freshNodes;
      this.treeNodes.set([...current]);
    }
  }

  /**
   * Recursive depth-first walk locating the TreeNode whose `data.path`
   * matches `path`. Returns `null` if no match exists in the currently
   * materialized tree (lazy: unloaded subtrees are invisible to this walk).
   */
  private findTreeNodeByPath(
    nodes: TreeNode[],
    path: string
  ): TreeNode | null {
    for (const n of nodes) {
      const fn = n.data as FileNode | undefined;
      if (fn?.path === path) return n;
      if (n.children) {
        const found = this.findTreeNodeByPath(n.children, path);
        if (found) return found;
      }
    }
    return null;
  }
}
