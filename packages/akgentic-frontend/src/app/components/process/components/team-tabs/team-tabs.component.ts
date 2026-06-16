import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { TabsModule } from 'primeng/tabs';
import { GraphComponent } from './graph/graph.component';
import { TreeComponent } from './tree/tree.component';

@Component({
  selector: 'app-team-tabs',
  standalone: true,
  imports: [CommonModule, TabsModule, GraphComponent, TreeComponent],
  templateUrl: './team-tabs.component.html',
  styleUrl: './team-tabs.component.scss',
})
export class TeamTabsComponent {}
