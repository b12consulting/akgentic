import { CommonModule } from '@angular/common';
import { Component, inject, NgZone, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { TreeNode } from 'primeng/api';
import { ButtonModule } from 'primeng/button';
import { DialogModule } from 'primeng/dialog';
import { TabViewModule } from 'primeng/tabview';
import { TextareaModule } from 'primeng/textarea';
import { TreeModule } from 'primeng/tree';
import { Subscription } from 'rxjs';
import { makeAgentNameUserFriendly } from '../../../lib/util';
import { ApiService } from '../../../services/api.service';
import { GraphDataService } from '../../../services/graph-data.service';
import {
  Selectable,
  SelectionService,
} from '../../../services/selection.service';
import { HumanRequestComponent } from '../../human-request.component';
import { EdgeInterface, NodeInterface } from '../../../models/types';

@Component({
  selector: 'app-tree',
  templateUrl: './tree.component.html',
  styleUrls: ['./tree.component.scss'],
  imports: [
    TreeModule,
    CommonModule,
    DialogModule,
    FormsModule,
    ButtonModule,
    TabViewModule,
    TextareaModule,
    HumanRequestComponent,
  ],
})
export class TreeComponent implements OnInit, OnDestroy {
  selectionService: SelectionService = inject(SelectionService);

  treeNodes: TreeNode[] = []; // Correct initialization as an array of TreeNode
  expandedKeys: { [key: string]: boolean } = {};

  nodes: NodeInterface[] = []; // Assuming nodes is an array
  edges: EdgeInterface[] = []; // Assuming edges is an array
  categories: any[] = []; // Assuming categories is an array

  private nodesSub: Subscription = new Subscription();
  private edgesSub: Subscription = new Subscription();
  private categoriesSub: Subscription = new Subscription();

  graphDataService: GraphDataService = inject(GraphDataService);
  apiService: ApiService = inject(ApiService);
  zone: NgZone = inject(NgZone);

  ngOnInit(): void {
    this.nodesSub = this.graphDataService.nodes$.subscribe(
      (nodes: NodeInterface[]) => {
        this.nodes = nodes;
        this.treeNodes = this.buildTree(nodes);
        this.expandAll();
      }
    );
    this.edgesSub = this.graphDataService.edges$.subscribe((updatedEdges) => {
      this.edges = updatedEdges;
    });
    this.categoriesSub = this.graphDataService.categories$.subscribe(
      (updatedCats) => {
        this.categories = updatedCats;
      }
    );
  }

  ngOnDestroy(): void {
    this.nodesSub.unsubscribe();
    this.edgesSub.unsubscribe(); // Unsubscribe from edgesSub
    this.categoriesSub.unsubscribe(); // Unsubscribe from categoriesSub
  }

  private buildTree(nodes: NodeInterface[]): TreeNode[] {
    const nodeMap: { [key: string]: TreeNode } = {};
    const categoryMembers: { [category: string]: any[] } = {};

    nodes.forEach((n) => {
      nodeMap[n.name] = {
        label:
          makeAgentNameUserFriendly(n.actorName) +
          (n?.humanRequests?.length ? ' 🙋' : ''),
        data: n,
        children: [], // Initialize children as an empty array
        expanded: false,
        styleClass:
          n.itemStyle?.borderColor === 'darkred'
            ? 'highlight-node'
            : '',
      };

      // Group nodes by category
      const category = n.category || 0; // Default to category 0 if undefined
      if (!categoryMembers[category]) {
        categoryMembers[category] = [];
      }
      categoryMembers[category].push(n);
    });

    const hasDarkRedNode = Object.values(nodeMap).some(
      (node) => node.styleClass === 'highlight-node'
    );

    if (hasDarkRedNode) {
      this.graphDataService.isLoading = true;
    } else {
      this.graphDataService.isLoading = false;
    }

    const tree: TreeNode[] = [];

    // Second pass to construct the tree structure and add category info
    nodes.forEach((n) => {
      const currentNode = nodeMap[n.name];

      // Append category (team number) to the label for the first node in each category
      const category = n.category || 0; // Default to category 0 if undefined
      if (
        category !== undefined &&
        categoryMembers[category].indexOf(n) === 0
      ) {
        const categoryLabel = ` (Team ${category})`;
        currentNode.label = currentNode.label + categoryLabel;
        currentNode.type = 'human';
        currentNode.key =
          this.graphDataService.categoryService.COLORS[category];
      }

      // Add the node to its parent or root
      if (n.parentId && nodeMap[n.parentId] && n.parentId !== n.name) {
        nodeMap[n.parentId].children!.push(currentNode);
      } else {
        tree.push(currentNode);
      }
    });

    return tree;
  }

  private expandAll() {
    this.expandedKeys = {};
    const recursivelyExpand = (nodes: TreeNode[]) => {
      // Takes an array of TreeNode
      nodes.forEach((node) => {
        this.expandedKeys[node.data.name] = true;
        node.expanded = true;
        if (node.children && node.children.length > 0) {
          recursivelyExpand(node.children);
        }
      });
    };
    recursivelyExpand(this.treeNodes);
  }

  onNodeClick(event: any) {
    const selectable: Selectable = {
      type: 'tree-node',
      data: event.node.data,
    };

    this.selectionService.handleSelection(selectable);
  }
}
