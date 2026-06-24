import { Component, EventEmitter, Input, Output, ViewChild } from '@angular/core';
import { CommonModule } from '@angular/common';
import { DialogModule } from 'primeng/dialog';
import { ButtonModule } from 'primeng/button';
import { FileUpload, FileUploadModule } from 'primeng/fileupload';
import { ProgressBarModule } from 'primeng/progressbar';
import { MessageModule } from 'primeng/message';

@Component({
  selector: 'app-upload-modal',
  standalone: true,
  imports: [
    CommonModule,
    DialogModule,
    ButtonModule,
    FileUploadModule,
    ProgressBarModule,
    MessageModule,
  ],
  templateUrl: './upload-modal.component.html',
  styleUrls: ['./upload-modal.component.scss'],
})
export class UploadModalComponent {
  @Input() visible = false;
  @Input() targetPath: string = '';
  @Output() visibleChange = new EventEmitter<boolean>();
  @Output() uploadComplete = new EventEmitter<void>();

  @ViewChild(FileUpload) fileUpload!: FileUpload;

  selectedFiles: File[] = [];
  uploading = false;
  uploadProgress = 0;
  errorMessage: string | null = null;
  successMessage: string | null = null;

  onHide() {
    this.visible = false;
    this.visibleChange.emit(false);
    this.resetModal();
  }

  onSelect(event: any) {
    this.selectedFiles = event.currentFiles;
    this.errorMessage = null;
    this.successMessage = null;
  }

  onRemove(event: any) {
    this.selectedFiles = this.selectedFiles.filter((f) => f !== event.file);
  }

  onClear() {
    this.selectedFiles = [];
    this.errorMessage = null;
    this.successMessage = null;
  }

  async uploadFiles() {
    if (this.selectedFiles.length === 0) {
      this.errorMessage = 'Please select at least one file to upload';
      return;
    }

    this.uploading = true;
    this.uploadProgress = 0;
    this.errorMessage = null;
    this.successMessage = null;

    try {
      // Emit the upload event with files
      // The parent component will handle the actual upload
      this.uploadComplete.emit();
      this.successMessage = `Successfully uploaded ${this.selectedFiles.length} file(s)`;

      // Auto-close after success
      setTimeout(() => {
        this.onHide();
      }, 1500);
    } catch (error: any) {
      this.errorMessage = error?.message || 'Failed to upload files';
    } finally {
      this.uploading = false;
      this.uploadProgress = 100;
    }
  }

  private resetModal() {
    // Clear the PrimeNG widget's internal files array first. The optional
    // chain guards against the ViewChild being undefined when the dialog
    // content has not yet been rendered (see issue #75 / Story 6-3 AC5).
    this.fileUpload?.clear();
    this.selectedFiles = [];
    this.uploading = false;
    this.uploadProgress = 0;
    this.errorMessage = null;
    this.successMessage = null;
  }

  getSelectedFiles(): File[] {
    return this.selectedFiles;
  }

  formatFileSize(bytes: number): string {
    if (!bytes) return '0 B';

    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));

    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
  }
}
