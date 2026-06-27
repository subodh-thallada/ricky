import * as path from "path";
import * as vscode from "vscode";
import { ApplyPreviewResult, ApplyProvider, ApplyResult, BenchOption, WorkspaceContext } from "../types";

type ParsedArtifact = {
  relativePath?: string;
  language?: string;
  content: string;
};

type PendingArtifact = {
  targetUri: vscode.Uri;
  displayPath: string;
  language?: string;
  originalContent: string;
  proposedContent: string;
  previewStartOffset: number;
  previewEndOffset: number;
};

type PendingSession = {
  optionId: string;
  optionTitle: string;
  artifacts: PendingArtifact[];
};

const textEncoder = new TextEncoder();
const previewDecoration = vscode.window.createTextEditorDecorationType({
  isWholeLine: false,
  backgroundColor: new vscode.ThemeColor("diffEditor.insertedTextBackground"),
  overviewRulerColor: new vscode.ThemeColor("diffEditorOverview.insertedForeground"),
  overviewRulerLane: vscode.OverviewRulerLane.Right,
  border: "1px solid rgba(115, 201, 145, 0.35)"
});

export class PreviewApplyProvider implements ApplyProvider {
  private pending?: PendingSession;

  async preview(option: BenchOption, workspaceContext: WorkspaceContext): Promise<ApplyPreviewResult> {
    if (this.pending) {
      await this.restorePreview(this.pending);
    }
    const session = await this.buildSession(option, workspaceContext);
    await this.showInlinePreview(session);
    this.pending = session;

    return {
      optionId: option.id,
      fileCount: session.artifacts.length,
      summary: `Loaded preview into ${session.artifacts[0]?.displayPath ?? "the active file"} as unsaved changes.`
    };
  }

  async applySelected(): Promise<ApplyResult | undefined> {
    if (!this.pending) {
      return undefined;
    }

    const session = this.pending;
    for (const artifact of session.artifacts) {
      const document = await vscode.workspace.openTextDocument(artifact.targetUri);
      if (document.getText() !== artifact.proposedContent) {
        throw new Error(`"${artifact.displayPath}" changed after preview. Re-preview before applying.`);
      }

      await ensureParentDirectory(artifact.targetUri);
      clearPreviewDecorations(document);
      const saved = await document.save();
      if (!saved) {
        throw new Error(`Bench could not save "${artifact.displayPath}".`);
      }
    }

    await this.revealFirstArtifact(session.artifacts[0]);
    this.pending = undefined;

    return {
      optionId: session.optionId,
      fileCount: session.artifacts.length,
      summary: `Applied preview to ${session.artifacts[0]?.displayPath ?? "the file"} and saved it.`
    };
  }

  async rejectSelected(): Promise<string | undefined> {
    if (!this.pending) {
      return undefined;
    }

    const session = this.pending;
    await this.restorePreview(session);
    this.pending = undefined;
    return `Restored ${session.artifacts[0]?.displayPath ?? "the file"} to its pre-preview contents.`;
  }

  getPendingOptionId(): string | undefined {
    return this.pending?.optionId;
  }

  private async buildSession(option: BenchOption, workspaceContext: WorkspaceContext): Promise<PendingSession> {
    const explicitArtifacts = parseExplicitArtifacts(option.generatedCode);
    const artifacts = shouldPreferActiveEditor(explicitArtifacts, workspaceContext)
      ? await this.buildEditorArtifact(explicitArtifacts[0]?.content ?? option.generatedCode, workspaceContext)
      : explicitArtifacts.some((artifact) => artifact.relativePath)
        ? await this.buildExplicitArtifacts(explicitArtifacts)
        : await this.buildEditorArtifact(explicitArtifacts[0]?.content ?? option.generatedCode, workspaceContext);

    if (!artifacts.length) {
      throw new Error("Bench could not determine where to preview this code.");
    }
    if (artifacts.length > 1) {
      throw new Error("Bench inline preview currently supports one file at a time. Ask for a narrower change or keep one file focused.");
    }

    return {
      optionId: option.id,
      optionTitle: option.title,
      artifacts
    };
  }

  private async buildExplicitArtifacts(artifacts: ParsedArtifact[]): Promise<PendingArtifact[]> {
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri;
    if (!workspaceRoot) {
      throw new Error("Open a workspace folder before applying file-based code suggestions.");
    }

    const pending: PendingArtifact[] = [];
    for (const artifact of artifacts) {
      if (!artifact.relativePath) {
        continue;
      }

      const targetUri = resolveWorkspacePath(workspaceRoot, artifact.relativePath);
      pending.push({
        targetUri,
        displayPath: normalizeDisplayPath(artifact.relativePath),
        language: artifact.language ?? languageFromPath(artifact.relativePath),
        originalContent: await readFileIfExists(targetUri),
        proposedContent: artifact.content,
        previewStartOffset: 0,
        previewEndOffset: artifact.content.length
      });
    }
    return pending;
  }

  private async buildEditorArtifact(code: string, workspaceContext: WorkspaceContext): Promise<PendingArtifact[]> {
    const editor = vscode.window.activeTextEditor;
    if (!editor) {
      throw new Error("Open the file you want Bench to modify, or return code with explicit file paths.");
    }

    const document = editor.document;
    const originalContent = document.getText();
    const selection = editor.selection;
    const proposedContent = selection.isEmpty
      ? insertAtCursor(originalContent, document.offsetAt(selection.active), code)
      : replaceSelection(
          originalContent,
          document.offsetAt(selection.start),
          document.offsetAt(selection.end),
          code
        );

    return [
      {
        targetUri: document.uri,
        displayPath: workspaceContext.activeFileName
          ? vscode.workspace.asRelativePath(workspaceContext.activeFileName, false)
          : path.basename(document.uri.fsPath),
        language: document.languageId,
        originalContent,
        proposedContent,
        previewStartOffset: selection.isEmpty ? document.offsetAt(selection.active) : document.offsetAt(selection.start),
        previewEndOffset: (
          selection.isEmpty
            ? document.offsetAt(selection.active) + trimTrailingWhitespace(code).length
            : document.offsetAt(selection.start) + trimTrailingWhitespace(code).length
        )
      }
    ];
  }

  private async showInlinePreview(session: PendingSession): Promise<void> {
    const artifact = session.artifacts[0];
    if (!artifact) {
      return;
    }
    const document = await this.openArtifactDocument(artifact);
    await replaceDocumentContents(document, artifact.proposedContent);
    applyPreviewDecorations(document, artifact);
  }

  private async revealFirstArtifact(artifact?: PendingArtifact): Promise<void> {
    if (!artifact) {
      return;
    }

    await this.openArtifactDocument(artifact);
  }

  private async restorePreview(session: PendingSession): Promise<void> {
    const artifact = session.artifacts[0];
    if (!artifact) {
      return;
    }
    const document = await this.openArtifactDocument(artifact);
    await replaceDocumentContents(document, artifact.originalContent);
    clearPreviewDecorations(document);
  }

  private async openArtifactDocument(artifact: PendingArtifact): Promise<vscode.TextDocument> {
    const document = await openOrCreateDocument(artifact);
    await vscode.window.showTextDocument(document, { preview: false, preserveFocus: false });
    return document;
  }
}

function parseExplicitArtifacts(generatedCode: string): ParsedArtifact[] {
  const headingMatches = [...generatedCode.matchAll(/(?:^|\n)###\s+([^\n]+)\n```([^\n]*)\n([\s\S]*?)```/g)];
  if (headingMatches.length) {
    return headingMatches.map((match) => ({
      relativePath: match[1].trim(),
      language: extractLanguage(match[2]),
      content: trimTrailingNewline(match[3])
    }));
  }

  const labeledMatches = [...generatedCode.matchAll(/(?:^|\n)(?:File|Path):\s*([^\n]+)\n```([^\n]*)\n([\s\S]*?)```/g)];
  if (labeledMatches.length) {
    return labeledMatches.map((match) => ({
      relativePath: match[1].trim(),
      language: extractLanguage(match[2]),
      content: trimTrailingNewline(match[3])
    }));
  }

  const fenceMatches = [...generatedCode.matchAll(/```([^\n`]*)\n([\s\S]*?)```/g)];
  if (fenceMatches.length) {
    return fenceMatches.map((match) => ({
      relativePath: extractPathFromFenceInfo(match[1]),
      language: extractLanguage(match[1]),
      content: trimTrailingNewline(match[2])
    }));
  }

  return [{ content: generatedCode.trim() }];
}

function extractLanguage(fenceInfo: string): string | undefined {
  const info = fenceInfo.trim();
  if (!info) {
    return undefined;
  }
  return info.split(/\s+/)[0];
}

function extractPathFromFenceInfo(fenceInfo: string): string | undefined {
  const pathMatch = fenceInfo.match(/(?:path|file|filename|title)=["']?([^"'\s]+)["']?/i);
  if (pathMatch?.[1]) {
    return normalizeDisplayPath(pathMatch[1]);
  }

  for (const token of fenceInfo.split(/\s+/)) {
    const cleaned = token.replace(/^["'`]+|["'`]+$/g, "");
    if (looksLikeRelativePath(cleaned)) {
      return normalizeDisplayPath(cleaned);
    }
  }

  return undefined;
}

function looksLikeRelativePath(value: string): boolean {
  return Boolean(value) && !path.isAbsolute(value) && /[\\/]/.test(value) && /\.[A-Za-z0-9]+$/.test(value);
}

function normalizeDisplayPath(value: string): string {
  return value.replace(/\\/g, "/").replace(/^\.\/+/, "");
}

function resolveWorkspacePath(workspaceRoot: vscode.Uri, relativePath: string): vscode.Uri {
  const normalizedPath = normalizeDisplayPath(relativePath);
  const resolvedPath = path.resolve(workspaceRoot.fsPath, normalizedPath);
  const rootPath = path.resolve(workspaceRoot.fsPath);
  const relative = path.relative(rootPath, resolvedPath);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new Error(`Bench refused to write outside the workspace: ${normalizedPath}`);
  }
  return vscode.Uri.file(resolvedPath);
}

async function readFileIfExists(uri: vscode.Uri): Promise<string> {
  try {
    const bytes = await vscode.workspace.fs.readFile(uri);
    return new TextDecoder().decode(bytes);
  } catch (error) {
    if (error instanceof vscode.FileSystemError) {
      return "";
    }
    return "";
  }
}

async function ensureParentDirectory(uri: vscode.Uri): Promise<void> {
  const parentUri = vscode.Uri.file(path.dirname(uri.fsPath));
  await vscode.workspace.fs.createDirectory(parentUri);
}

async function openOrCreateDocument(artifact: PendingArtifact): Promise<vscode.TextDocument> {
  try {
    return await vscode.workspace.openTextDocument(artifact.targetUri);
  } catch {
    await ensureParentDirectory(artifact.targetUri);
    await vscode.workspace.fs.writeFile(artifact.targetUri, textEncoder.encode(artifact.originalContent));
    return vscode.workspace.openTextDocument(artifact.targetUri);
  }
}

async function replaceDocumentContents(document: vscode.TextDocument, content: string): Promise<void> {
  const editor = await vscode.window.showTextDocument(document, { preview: false, preserveFocus: false });
  const fullRange = new vscode.Range(
    document.positionAt(0),
    document.positionAt(document.getText().length)
  );
  const success = await editor.edit((editBuilder) => {
    editBuilder.replace(fullRange, content);
  });
  if (!success) {
    throw new Error(`Bench could not update "${path.basename(document.uri.fsPath)}".`);
  }
}

function applyPreviewDecorations(document: vscode.TextDocument, artifact: PendingArtifact): void {
  const editor = vscode.window.visibleTextEditors.find((item) => item.document.uri.toString() === document.uri.toString());
  if (!editor) {
    return;
  }

  const safeStart = Math.max(0, Math.min(artifact.previewStartOffset, document.getText().length));
  const safeEnd = Math.max(safeStart, Math.min(artifact.previewEndOffset, document.getText().length));
  const range = new vscode.Range(document.positionAt(safeStart), document.positionAt(safeEnd));
  editor.setDecorations(previewDecoration, range.isEmpty ? [] : [range]);
}

function clearPreviewDecorations(document: vscode.TextDocument): void {
  const editor = vscode.window.visibleTextEditors.find((item) => item.document.uri.toString() === document.uri.toString());
  editor?.setDecorations(previewDecoration, []);
}

function replaceSelection(source: string, start: number, end: number, code: string): string {
  return `${source.slice(0, start)}${trimTrailingWhitespace(code)}${source.slice(end)}`;
}

function insertAtCursor(source: string, offset: number, code: string): string {
  const trimmedCode = trimTrailingWhitespace(code);
  const prefix = offset > 0 && source[offset - 1] !== "\n" ? "\n" : "";
  const suffix = offset < source.length && source[offset] !== "\n" ? "\n" : "";
  return `${source.slice(0, offset)}${prefix}${trimmedCode}${suffix}${source.slice(offset)}`;
}

function trimTrailingWhitespace(value: string): string {
  return value.replace(/\s+$/u, "");
}

function trimTrailingNewline(value: string): string {
  return value.replace(/\n+$/u, "");
}

function languageFromPath(filePath: string): string | undefined {
  const extension = path.extname(filePath).toLowerCase();
  const map: Record<string, string> = {
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".py": "python",
    ".json": "json",
    ".md": "markdown",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml"
  };
  return map[extension];
}

function shouldPreferActiveEditor(
  artifacts: ParsedArtifact[],
  workspaceContext: WorkspaceContext
): boolean {
  if (!vscode.window.activeTextEditor || !workspaceContext.activeFileName) {
    return false;
  }

  if (artifacts.length !== 1) {
    return false;
  }

  const artifact = artifacts[0];
  if (!artifact.relativePath) {
    return true;
  }

  const normalizedTarget = normalizeDisplayPath(artifact.relativePath);
  const normalizedActive = normalizeDisplayPath(vscode.workspace.asRelativePath(workspaceContext.activeFileName, false));
  if (normalizedTarget === normalizedActive) {
    return true;
  }

  const genericGeneratedPrefixes = ["src/generated/", "generated/", "bench_preview/"];
  return genericGeneratedPrefixes.some((prefix) => normalizedTarget.startsWith(prefix));
}
