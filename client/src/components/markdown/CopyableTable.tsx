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
        <div className="group/table relative">
            {/* Hover toolbar over the table's corner: reserving a row for it
                left a blank band above every table. */}
            <div className="pointer-events-none absolute top-1 right-1 z-10 flex items-center gap-0.5 rounded-md border border-border/60 bg-background/95 p-0.5 opacity-0 shadow-sm backdrop-blur-sm transition-opacity duration-150 group-hover/table:pointer-events-auto group-hover/table:opacity-100 focus-within:pointer-events-auto focus-within:opacity-100">
                <button
                    onClick={handleCopy}
                    className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
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
                    className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                    title="Export as CSV"
                    type="button"
                >
                    <Download className="h-3.5 w-3.5" />
                    <span>CSV</span>
                </button>
            </div>
            {/* The frame is the block; the table itself carries no margins,
                and the header row gets the top padding prose leaves off. */}
            <div className="overflow-x-auto rounded-md border border-border/50 px-3 [&_thead_th]:pt-2 [&>table]:my-0">
                <table ref={tableRef} className={className ?? "min-w-full border-collapse"} {...props}>
                    {children}
                </table>
            </div>
        </div>
    );
}
