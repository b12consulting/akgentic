// frontend/src/app/home/config-editor/config-editor.component.ts

import { CommonModule } from '@angular/common';
import { Component, inject, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { BehaviorSubject } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { ButtonModule } from 'primeng/button';
import { TagModule } from 'primeng/tag';

@Component({
  selector: 'app-config-editor',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ButtonModule,
    TagModule,
  ],
  templateUrl: './config-editor.component.html',
  styleUrls: ['./config-editor.component.scss'],
})
export class ConfigEditorComponent implements OnInit {
  apiService: ApiService = inject(ApiService);

  catalogEntries$ = new BehaviorSubject<any[]>([]);
  loading = false;

  ngOnInit() {
    this.loadCatalogEntries();
  }

  async loadCatalogEntries() {
    this.loading = true;
    try {
      const response = await this.apiService.getTeamConfigs();
      const entries = Array.isArray(response)
        ? response
        : Object.keys(response).map((key) => ({ id: key, name: key }));
      this.catalogEntries$.next(entries);
    } catch (error) {
      console.error('Failed to load catalog entries:', error);
    } finally {
      this.loading = false;
    }
  }
}
