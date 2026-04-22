import { Component, inject, Input, DestroyRef } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { CommonModule } from '@angular/common';
import {
  FormArray,
  FormBuilder,
  FormGroup,
  ReactiveFormsModule,
  Validators,
} from '@angular/forms';

import { BehaviorSubject } from 'rxjs';

import { ButtonModule } from 'primeng/button';
import { TextareaModule } from 'primeng/textarea';
import { MessageService } from 'primeng/api';

import { AkgentService } from '../../../services/akgent.service';
import { ConfigService } from '../../../services/config.service';
import { ActorMessageService } from '../../../services/message.service';

@Component({
  selector: 'app-akgent-state',
  imports: [CommonModule, ReactiveFormsModule, TextareaModule, ButtonModule],
  templateUrl: './akgent-state.component.html',
  styleUrl: './akgent-state.component.scss',
})
export class AkgentStateComponent {
  @Input() state$!: BehaviorSubject<any>;
  @Input() agentId!: string;

  akgentService: AkgentService = inject(AkgentService);
  actorMessageService: ActorMessageService = inject(ActorMessageService);
  private config = inject(ConfigService);
  toastService: MessageService = inject(MessageService);
  formBuider: FormBuilder = inject(FormBuilder);
  private destroyRef = inject(DestroyRef);

  schemaFields!: any[];
  dynamicForm!: FormGroup;

  ngOnInit(): void {
    this.state$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((data) => {
      this.dynamicForm = this.formBuider.group({});
      this.generateForm(data);
    });
  }

  generateForm(data: any): void {
    if (!data) return;
    const schema = data.schema || {};

    if (!schema.properties) {
      // V2 sends empty schema -- display raw state as JSON
      this.schemaFields = [];
      return;
    }

    this.schemaFields = Object.keys(schema.properties).map((key) => {
      const field = schema.properties[key];
      const isRequired = schema.required?.includes(key) || false;

      const defaultValue =
        field.type === 'array'
          ? JSON.stringify(data?.state?.[key] || '', null, 4)
          : data?.state?.[key] || '';

      const disabled =
        field.type === 'array'
          ? true
          : field.readOnly || field.readonly || false;

      const control = this.formBuider.control(
        {
          value: defaultValue,
          disabled: disabled,
        },
        [...(isRequired ? [Validators.required] : [])]
      );

      this.dynamicForm.addControl(key, control);

      return {
        name: key,
        label: this.mapKeysToUserFriendlyNames(
          field.description || field.title || key
        ),
        type:
          field.type || field.anyOf?.find((f: any) => f.type != 'null')?.type,
        controls:
          field.type === 'array'
            ? (this.dynamicForm.get(key) as FormArray).controls
            : null,
      };
    });
  }

  mapKeysToUserFriendlyNames(key: string): string {
    if (this.config.production) {
      if (key === 'Backstory') {
        return 'Objectives';
      } else if (key === 'Support Ticket') {
        return 'Tasks';
      } else {
        return key;
      }
    }
    return key;
  }

  addArrayItem(fieldName: string): void {
    const arrayControl = this.dynamicForm.get(fieldName) as FormArray;
    arrayControl.push(this.formBuider.control(''));
  }

  removeArrayItem(fieldName: string, index: number): void {
    const arrayControl = this.dynamicForm.get(fieldName) as FormArray;
    arrayControl.removeAt(index);
  }

  onSubmit(): void {
    this.toastService.add({
      severity: 'info',
      summary: 'Read Only',
      detail: 'State editing is not available in V2',
      life: 3000,
    });
  }

  getDirtyValues(form: FormGroup | FormArray): any {
    const dirtyValues: any = {};
    Object.keys(form.controls).forEach((key) => {
      const currentControl = form.get(key);
      if (currentControl?.dirty) {
        const recursiveCall =
          currentControl instanceof FormGroup ||
          currentControl instanceof FormArray;
        dirtyValues[key] = recursiveCall
          ? this.getDirtyValues(currentControl)
          : currentControl.value;
      }
    });
    return dirtyValues;
  }
}
