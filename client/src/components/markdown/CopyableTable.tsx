'use client';

import React, { useCallback, useRef, useState } from 'react';
import { Copy, Check, Download } from 'lucide-react';

// Extract table data as TSV (tab-separated values)
function extractTableData(tableElement: HTMLTableElement): string {
    const rows: string[][] = [];

    // Process header rows
    const thead = tableElement.querySelector('thead');
    if (thead) {
        thead.querySelectorAll('tr').forEach(tr => {
            const cells: string[] = [];
            tr.querySelectorAll('th, td').forEach(cell => {
                cells.push((cell.textContent || '').trim());
            });
            if (cells.length > 0) rows.push(cells);
        });
    }

    // Process body rows
    const tbody = tableElement.querySelector('tbody');
    if (tbody) {
        tbody.querySelectorAll('tr').forEach(tr => {
            const cells: string[] = [];
            tr.querySelectorAll('th, td').forEach(cell => {
                cells.push((cell.textContent || '').trim());
            });
            if (cells.length > 0) rows.push(cells);
        });
    }

    // If no thead/tbody, try direct tr children
    if (rows.length === 0) {
        tableElement.querySelectorAll('tr').forEach(tr => {
            const cells: string[] = [];
            tr.querySelectorAll('th, td').forEach(cell => {
                cells.push((cell.textContent || '').trim());
            });
            if (cells.length > 0) rows.push(cells);
        });
    }

    return rows.map(row => row.join('\t')).join('\n');
}

// Convert table data to CSV format
function tableDataToCsv(tableElement: HTMLTableElement): string {
    const rows: string[][] = [];

    const processRow = (tr: Element) => {
        const cells: string[] = [];
        tr.querySelectorAll('th, td').forEach(cell => {
            // Escape quotes and wrap in quotes if contains comma, quote, or newline
            let cellText = (cell.textContent || '').trim();
            if (cellText.includes('"') || cellText.includes(',') || cellText.includes('\n')) {
                cellText = '"' + cellText.replace(/"/g, '""') + '"';
            }
            cells.push(cellText);
        });
        if (cells.length > 0) rows.push(cells);
    };

    const thead = tableElement.querySelector('thead');
    if (thead) thead.querySelectorAll('tr').forEach(processRow);

    const tbody = tableElement.querySelector('tbody');
    if (tbody) tbody.querySelectorAll('tr').forEach(processRow);

    if (rows.length === 0) {
        tableElement.querySelectorAll('tr').forEach(processRow);
    }

    return rows.map(row => row.join(',')).join('\n');
}

/** Markdown table with a hover toolbar: copy as TSV, export as CSV. */
export function CopyableTable({
    children,
    className,
    // The markdown renderer passes its hast node; keep it off the DOM.
    node: _node,
    ...props
}: React.TableHTMLAttributes<HTMLTableElement> & { children?: React.ReactNode; node?: unknown }) {
    const tableRef = useRef<HTMLTableElement>(null);
    const [copied, setCopied] = useState(false);

    const handleCopy = useCallback(async () => {
        if (!tableRef.current) return;

        const tableData = extractTableData(tableRef.current);

        try {
            await navigator.clipboard.writeText(tableData);
            setCopied(true);
            setTimeout(() => setCopied(false), 2000);
        } catch (err) {
            console.error('Failed to copy table data:', err);
        }
    }, []);

    const handleExportCsv = useCallback(() => {
        if (!tableRef.current) return;

        const csvData = tableDataToCsv(tableRef.current);
        const blob = new Blob([csvData], { type: 'text/csv;charset=utf-8;' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = 'table-data.csv';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
    }, []);

    return (
        <div className="group/table">
            {/* Toolbar */}
            <div className="flex items-center justify-between px-3 py-1.5 bg-accent/50 rounded-t-md border border-b-0 border-border/50 opacity-0 group-hover/table:opacity-100 transition-opacity duration-200">
                <button
                    onClick={handleCopy}
                    className="inline-flex items-center gap-1.5 px-2 py-1 text-xs font-medium rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                    title="Copy to clipboard"
                    type="button"
                >
                    {copied ? (
                        <>
                            <Check className="h-3.5 w-3.5 text-green-500" />
                            <span className="text-green-500">Copied</span>
                        </>
                    ) : (
                        <>
                            <Copy className="h-3.5 w-3.5" />
                            <span>Copy</span>
                        </>
                    )}
                </button>
                <button
                    onClick={handleExportCsv}
                    className="inline-flex items-center gap-1.5 px-2 py-1 text-xs font-medium rounded hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"
                    title="Export as CSV"
                    type="button"
                >
                    <Download className="h-3.5 w-3.5" />
                    <span>Export CSV</span>
                </button>
            </div>
            {/* Table */}
            <div className="overflow-x-auto border border-border/50 rounded-b-md group-hover/table:rounded-b-md group-hover/table:rounded-t-none rounded-md px-3">
                <table ref={tableRef} className={className ?? "min-w-full border-collapse"} {...props}>
                    {children}
                </table>
            </div>
        </div>
    );
}
