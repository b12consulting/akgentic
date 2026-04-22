import { CUSTOM_ELEMENTS_SCHEMA } from '@angular/core';
import { ComponentFixture, TestBed, fakeAsync, tick } from '@angular/core/testing';
import { NoopAnimationsModule } from '@angular/platform-browser/animations';
import { FileUpload } from 'primeng/fileupload';

import { UploadModalComponent } from './upload-modal.component';

// --------------------------------------------------------------------
// Test helpers
// --------------------------------------------------------------------

function makeFile(name: string, content = 'x'): File {
  return new File([content], name);
}

function installFileUploadSpy(component: UploadModalComponent): jasmine.Spy {
  const clearSpy = jasmine.createSpy('clear');
  component.fileUpload = { clear: clearSpy } as unknown as FileUpload;
  return clearSpy;
}

// --------------------------------------------------------------------
// Tests
// --------------------------------------------------------------------

describe('UploadModalComponent', () => {
  let component: UploadModalComponent;
  let fixture: ComponentFixture<UploadModalComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [UploadModalComponent, NoopAnimationsModule],
    })
      .overrideComponent(UploadModalComponent, {
        set: {
          // Strip PrimeNG template rendering; we stub the ViewChild manually
          // so tests never require the dialog content to be in the DOM.
          imports: [],
          schemas: [CUSTOM_ELEMENTS_SCHEMA],
          template: '',
        },
      })
      .compileComponents();

    fixture = TestBed.createComponent(UploadModalComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('scenario 1 — clears FileUpload widget after successful upload', fakeAsync(() => {
    const clearSpy = installFileUploadSpy(component);
    component.selectedFiles = [makeFile('a.md'), makeFile('b.md')];

    component.uploadFiles();
    // uploadFiles is async; let the microtask queue drain before advancing
    // the clock past the success-path 1500ms auto-close setTimeout.
    tick(0);
    tick(1500);

    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(component.selectedFiles).toEqual([]);
    expect(component.visible).toBe(false);
  }));

  it('scenario 2 — retains FileUpload widget on upload error', fakeAsync(() => {
    const clearSpy = installFileUploadSpy(component);
    component.selectedFiles = [makeFile('a.md')];

    // Force the success path inside the try-block to throw so that the
    // catch-branch at upload-modal.component.ts:78-80 executes. The auto-close
    // setTimeout is only scheduled on success, so resetModal() must NOT run.
    spyOn(component.uploadComplete, 'emit').and.throwError('network down');

    component.uploadFiles();
    tick(0);
    tick(2000); // well past the 1500ms success timeout that never schedules

    expect(clearSpy).not.toHaveBeenCalled();
    expect(component.selectedFiles.length).toBe(1);
    expect(component.errorMessage).toBe('network down');
  }));

  it('scenario 3 — clears FileUpload widget on manual cancel (onHide)', () => {
    const clearSpy = installFileUploadSpy(component);
    component.selectedFiles = [makeFile('a.md')];
    component.visible = true;

    component.onHide();

    expect(clearSpy).toHaveBeenCalledTimes(1);
    expect(component.selectedFiles).toEqual([]);
    expect(component.visible).toBe(false);
  });

  it('scenario 4 — resetModal is safe when FileUpload ViewChild is undefined', () => {
    // Simulate the dialog-never-opened case: ViewChild has not resolved yet.
    component.fileUpload = undefined as unknown as FileUpload;
    component.selectedFiles = [makeFile('a.md')];

    expect(() => component.onHide()).not.toThrow();
    expect(component.selectedFiles).toEqual([]);
  });
});
