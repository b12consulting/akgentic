import { CommonModule } from '@angular/common';
import {
  Component,
  inject,
  NgZone,
  OnDestroy,
  OnInit,
  Input,
  HostBinding,
} from '@angular/core';
import { trigger, style, transition, animate } from '@angular/animations';
import { ActivatedRoute } from '@angular/router';
import { BehaviorSubject, Subscription } from 'rxjs';

import { ButtonModule } from 'primeng/button';
import { CardModule } from 'primeng/card';
import { FieldsetModule } from 'primeng/fieldset';
import { TagModule } from 'primeng/tag';
import { TabViewModule } from 'primeng/tabview';
import { DialogModule } from 'primeng/dialog';
import { MarkdownModule } from 'ngx-markdown';

import { GraphChart } from 'echarts/charts';
import {
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import * as echarts from 'echarts/core';
import { EChartsCoreOption } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { NgxEchartsDirective, provideEchartsCore } from 'ngx-echarts';

import { KGStateReducer } from '../../services/kg-state.reducer';
import { ActorMessageService } from '../../services/message.service';

echarts.use([
  CanvasRenderer,
  TitleComponent,
  TooltipComponent,
  GraphChart,
  LegendComponent,
]);

interface KnowledgeGraphEntity {
  // V2 wire: uuid string, required. The projection from `KGStateReducer`
  // always carries `id`; component keeps it optional only to tolerate any
  // legacy fixture/test that builds entities manually without an id.
  id?: string;
  // V2 wire: optional flag (backend only sends it when `True`).
  is_root?: boolean;
  name?: string;
  entity_type?: string;
  description?: string;
  observations?: any[];
}

interface KnowledgeGraphRelation {
  // V2 wire: uuid string, required (see note above on id optionality).
  id?: string;
  from_entity?: string;
  to_entity?: string;
  relation_type?: string;
  // V2 wire: optional, defaults to "" on the wire.
  description?: string;
}

interface KnowledgeGraphData {
  nodes: KnowledgeGraphEntity[];
  edges: KnowledgeGraphRelation[];
}

@Component({
  selector: 'app-knowledge-graph',
  imports: [
    CommonModule,
    NgxEchartsDirective,
    ButtonModule,
    FieldsetModule,
    CardModule,
    TagModule,
    TabViewModule,
    DialogModule,
    MarkdownModule,
  ],
  templateUrl: './knowledge-graph.component.html',
  styleUrl: './knowledge-graph.component.scss',
  providers: [provideEchartsCore({ echarts })],
  animations: [
    trigger('slideIn', [
      transition(':enter', [
        style({ transform: 'translateX(100%)', opacity: 0 }),
        animate(
          '300ms ease-in-out',
          style({ transform: 'translateX(0)', opacity: 1 })
        ),
      ]),
      transition(':leave', [
        animate(
          '300ms ease-in-out',
          style({ transform: 'translateX(100%)', opacity: 0 })
        ),
      ]),
    ]),
  ],
})
export class KnowledgeGraphComponent implements OnInit, OnDestroy {
  @Input() isModal = false;
  @Input() processId?: string; // Allow process ID to be passed as input for modal mode

  @HostBinding('class.modal-mode') get modalMode() {
    return this.isModal;
  }

  isModalView = false;
  showKGModal = false;

  openKGModal(): void {
    this.showKGModal = true;
  }

  zone: NgZone = inject(NgZone);
  route: ActivatedRoute = inject(ActivatedRoute);
  messageService: ActorMessageService = inject(ActorMessageService);
  // Story 6.2 (ADR-005 §Decision 4): subscribe to the pure selector instead
  // of the former `messageService.knowledgeGraph$` passthrough (deleted).
  private readonly kgReducer: KGStateReducer = inject(KGStateReducer);

  currentProcessId: string = '';
  graphData$ = new BehaviorSubject<KnowledgeGraphData | null>(null);
  error$ = new BehaviorSubject<string | null>(null);

  echartsInstance: any;
  graphOptions: EChartsCoreOption = {};
  selectedNode$ = new BehaviorSubject<KnowledgeGraphEntity | null>(null);

  // Tab state management
  activeTabIndex = 0;

  // Node properties sidebar visibility
  showNodeProperties = false;

  private subscriptions: Subscription[] = [];

  ngOnInit(): void {
    // Get process ID either from input (modal mode) or route (normal mode)
    this.currentProcessId = this.processId || this.route.snapshot.params['id'];

    // Initialize with empty data to ensure chart is always created
    this.graphData$.next({ nodes: [], edges: [] });

    this.subscriptions.push(
      this.kgReducer.knowledgeGraph$.subscribe((data) => {
        this.graphData$.next(data || { nodes: [], edges: [] });
        this.error$.next(null);
      })
    );

    // Subscribe to data changes to update the chart
    this.subscriptions.push(
      this.graphData$.subscribe((data) => {
        this.updateChart(data || { nodes: [], edges: [] });
      })
    );
  }

  ngOnDestroy(): void {
    this.subscriptions.forEach((sub) => sub.unsubscribe());
  }

  onChartInit(ec: any): void {
    this.echartsInstance = ec;
    this.echartsInstance.resize();

    // Create the chart with current data when ECharts instance becomes available
    const currentData = this.graphData$.value;
    this.createChart(currentData || { nodes: [], edges: [] });

    // Handle click events - open/close sidebar
    this.echartsInstance.on('click', (params: any) => {
      this.zone.run(() => {
        if (params.dataType === 'node') {
          console.log('Node clicked:', params.data);
          // Open sidebar and set the selected node
          this.showNodeProperties = true;
          this.selectedNode$.next({
            name: params.data.name,
            entity_type: params.data.category,
            description: params.data.description,
            observations: params.data.observations,
          });
        } else if (params.dataType === 'edge') {
          console.log('Edge clicked:', params.data);
          // Close sidebar when clicking on edge
          this.showNodeProperties = false;
          this.selectedNode$.next(null);
        } else {
          // Close sidebar when clicking on empty space
          this.showNodeProperties = false;
          this.selectedNode$.next(null);
        }
      });
    });

    // Handle hover events - update content if sidebar is open
    this.echartsInstance.on('mouseover', (params: any) => {
      if (params.dataType === 'node' && this.showNodeProperties) {
        console.log('Node hovered:', params.data);
        // Update the selected node content on hover if sidebar is open using Angular zone
        this.zone.run(() => {
          this.selectedNode$.next({
            name: params.data.name,
            entity_type: params.data.category,
            description: params.data.description,
            observations: params.data.observations,
          });
        });
      }
    });
  }

  private createChart(data: KnowledgeGraphData): void {
    const { nodes, links, categories } = this.processGraphData(data);

    const chartOptions: EChartsCoreOption = {
      title: {
        text: data.nodes.length === 0 ? 'No Knowledge Graph' : null,
        top: '50%',
        left: 'center',
      },
      legend: {
        data: categories.length > 0 ? categories.map((c) => c.name) : [],
        show: categories.length > 0,
        top: '8px', // Distance from top
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          force: {
            repulsion: 500,
            edgeLength: [50, 200],
            gravity: 0.1,
          },
          label: {
            show: true,
            position: 'bottom',
            distance: 5,
            fontSize: 10,
          },
          edgeLabel: {
            show: true,
            fontSize: 8,
          },
          symbol: 'circle',
          symbolSize: 30,
          edgeSymbol: ['none', 'arrow'],
          edgeSymbolSize: 8,
          lineStyle: {
            opacity: 0.7,
            width: 2,
            curveness: 0.1,
          },
          emphasis: {
            focus: 'adjacency',
            lineStyle: {
              width: 3,
              opacity: 1,
            },
            itemStyle: {
              shadowBlur: 10,
              shadowColor: 'rgba(0, 0, 0, 0.3)',
            },
          },
          data: nodes,
          links: links,
          categories: categories.length > 0 ? categories : undefined,
          zoom: 1,
        },
      ],
    };

    this.graphOptions = chartOptions;
    if (this.echartsInstance) {
      this.echartsInstance.setOption(chartOptions, true);
      this.echartsInstance.resize();
    }
  }

  private updateChart(data: KnowledgeGraphData): void {
    if (!this.echartsInstance) return;

    const { nodes, links, categories } = this.processGraphData(data);

    // Complete series update with all necessary configuration
    this.echartsInstance.setOption({
      title: {
        text: data.nodes.length === 0 ? 'No Knowledge Graph' : null,
      },
      legend: {
        data: categories.length > 0 ? categories.map((c) => c.name) : [],
        show: categories.length > 0,
        top: '8px',
      },
      series: [
        {
          data: nodes,
          links: links,
          categories: categories.length > 0 ? categories : undefined,
        },
      ],
    });
  }

  /**
   * Process graph data and transform it into ECharts format
   * @param data The raw knowledge graph data
   * @returns Processed nodes, links, categories, and entity types
   */
  private processGraphData(data: KnowledgeGraphData): {
    nodes: any[];
    links: any[];
    categories: any[];
    entityTypes: string[];
  } {
    // Create categories for legend - include all entity types (even undefined/null)
    const allEntityTypes = (data.nodes || []).map(
      (n) => n.entity_type || 'unknown'
    );
    const entityTypes = [...new Set(allEntityTypes)];

    // Transform entities into ECharts nodes format
    const nodes = (data.nodes || []).map((entity, index) => ({
      id: entity.name || `entity-${index}`,
      name: entity.name || `Entity ${index}`,
      category: entity.entity_type || 'unknown',
      itemStyle: {
        color: this.getNodeColor(entityTypes, entity.entity_type || 'unknown'),
      },
      // Add description as additional data for tooltips
      description: entity.description,
      observations: entity.observations,
    }));

    // Transform relations into ECharts links format
    const links = (data.edges || []).map((relation, index) => ({
      id: `relation-${index}`,
      source: relation.from_entity || '',
      target: relation.to_entity || '',
      name: relation.relation_type || 'relation',
      label: {
        show: true,
        formatter: relation.relation_type || '',
      },
    }));

    const categories = entityTypes.map((type) => ({
      name: type,
      itemStyle: {
        color: this.getNodeColor(entityTypes, type),
      },
    }));

    return { nodes, links, categories, entityTypes };
  }

  /**
   * Get color for a node based on its entity type's position in the entityTypes array
   * @param entityTypes Array of all unique entity types in the graph
   * @param type The specific entity type to get color for
   * @returns A color string from the predefined color palette
   */
  private getNodeColor(entityTypes: string[], type: string): string {
    const COLORS = [
      '#6A9BB6', // Index 1 - Blue
      '#3ba272', // Index 2 - Green
      '#ee6666', // Index 3 - Red
      '#fc8452', // Index 4 - Orange
      '#73c0de', // Index 5 - Light Blue
      '#5470c6', // Index 6 - Dark Blue
      '#9a60b4', // Index 7 - Purple
      '#ea7ccc', // Index 8 - Pink
      '#91cc75', // Index 9 - Light Green
      '#fac858', // Index 10 - Yellow
    ];

    // Find the index of this type in the entityTypes array
    const index = entityTypes.indexOf(type);

    // Use the index to select a color from the array (with wraparound for more than 10 types)
    const colorIndex = index >= 0 ? index % COLORS.length : 0;
    return COLORS[colorIndex];
  }
}
