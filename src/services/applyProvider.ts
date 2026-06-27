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
  private readonly pendingSessions = new Map<string, PendingSession>();
  private readonly fileOwners = new Map<string, string>();

  async preview(sessionId: string, option: BenchOption, workspaceContext: WorkspaceContext): Promise<ApplyPreviewResult> {
    const session = await this.buildSession(option, workspaceContext);
    const deactivatedSessionIds: string[] = [];

    for (const artifact of session.artifacts) {
      const existingOwner = this.fileOwners.get(artifact.targetUri.toString());
      if (existingOwner && existingOwner !== sessionId) {
        const existingSession = this.pendingSessions.get(existingOwner);
        if (existingSession) {
          await this.restorePreview(existingSession);
          this.pendingSessions.delete(existingOwner);
          deactivatedSessionIds.push(existingOwner);
          for (const existingArtifact of existingSession.artifacts) {
            this.fileOwners.delete(existingArtifact.targetUri.toString());
          }
        }
      }
    }

    const existingSession = this.pendingSessions.get(sessionId);
    if (existingSession) {
      await this.restorePreview(existingSession);
      for (const existingArtifact of existingSession.artifacts) {
        this.fileOwners.delete(existingArtifact.targetUri.toString());
      }
    }

    await this.showInlinePreview(session);
    this.pendingSessions.set(sessionId, session);
    for (const artifact of session.artifacts) {
      this.fileOwners.set(artifact.targetUri.toString(), sessionId);
    }

    return {
      optionId: option.id,
      fileCount: session.artifacts.length,
      summary: `Loaded preview into ${session.artifacts[0]?.displayPath ?? "the active file"} as unsaved changes.`,
      deactivatedSessionIds
    };
  }

  async applySelected(sessionId: string): Promise<ApplyResult | undefined> {
    const session = this.pendingSessions.get(sessionId);
    if (!session) {
      return undefined;
    }

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
    this.pendingSessions.delete(sessionId);
    for (const artifact of session.artifacts) {
      this.fileOwners.delete(artifact.targetUri.toString());
    }

    return {
      optionId: session.optionId,
      fileCount: session.artifacts.length,
      summary: `Applied preview to ${session.artifacts[0]?.displayPath ?? "the file"} and saved it.`
    };
  }

  async rejectSelected(sessionId: string): Promise<string | undefined> {
    const session = this.pendingSessions.get(sessionId);
    if (!session) {
      return undefined;
    }

    await this.restorePreview(session);
    this.pendingSessions.delete(sessionId);
    for (const artifact of session.artifacts) {
      this.fileOwners.delete(artifact.targetUri.toString());
    }
    return `Restored ${session.artifacts[0]?.displayPath ?? "the file"} to its pre-preview contents.`;
  }

  hasPendingSession(sessionId: string): boolean {
    return this.pendingSessions.has(sessionId);
  }

  private async buildSession(option: BenchOption, workspaceContext: WorkspaceContext): Promise<PendingSession> {
    const explicitArtifacts = parseExplicitArtifacts(option.generatedCode);
    const artifacts = shouldPreferActiveEditor(explicitArtifacts, workspaceContext)
      ? await this.buildInferredArtifact(option, explicitArtifacts[0]?.content ?? option.generatedCode, workspaceContext)
      : explicitArtifacts.some((artifact) => artifact.relativePath)
        ? await this.buildExplicitArtifacts(explicitArtifacts)
        : await this.buildInferredArtifact(option, explicitArtifacts[0]?.content ?? option.generatedCode, workspaceContext);

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
        originalContent: await this.readBaseContent(targetUri),
        proposedContent: artifact.content,
        previewStartOffset: 0,
        previewEndOffset: artifact.content.length
      });
    }
    return pending;
  }

  private async buildInferredArtifact(
    option: BenchOption,
    code: string,
    workspaceContext: WorkspaceContext
  ): Promise<PendingArtifact[]> {
    const target = await inferTargetDocument(option, code, workspaceContext);
    if (!target) {
      throw new Error("Open the file you want Bench to modify, or return code with explicit file paths.");
    }

    const { document, displayPath, selection } = target;
    const originalContent = this.getBaseContentForDocument(document);
    const placement = inferPlacementRange(
      originalContent,
      document.languageId,
      selection && !selection.isEmpty
        ? {
            startOffset: document.offsetAt(selection.start),
            endOffset: document.offsetAt(selection.end)
          }
        : undefined,
      code
    );
    const proposedContent = replaceSelection(
      originalContent,
      placement.startOffset,
      placement.endOffset,
      code
    );

    return [
      {
        targetUri: document.uri,
        displayPath,
        language: document.languageId,
        originalContent,
        proposedContent,
        previewStartOffset: placement.startOffset,
        previewEndOffset: placement.startOffset + trimTrailingWhitespace(code).length
      }
    ];
  }

  private getBaseContentForDocument(document: vscode.TextDocument): string {
    const existingOwner = this.fileOwners.get(document.uri.toString());
    if (!existingOwner) {
      return document.getText();
    }

    const existingSession = this.pendingSessions.get(existingOwner);
    const existingArtifact = existingSession?.artifacts.find(
      (artifact) => artifact.targetUri.toString() === document.uri.toString()
    );
    return existingArtifact?.originalContent ?? document.getText();
  }

  private async readBaseContent(targetUri: vscode.Uri): Promise<string> {
    const existingOwner = this.fileOwners.get(targetUri.toString());
    if (!existingOwner) {
      return readFileIfExists(targetUri);
    }

    const existingSession = this.pendingSessions.get(existingOwner);
    const existingArtifact = existingSession?.artifacts.find(
      (artifact) => artifact.targetUri.toString() === targetUri.toString()
    );
    return existingArtifact?.originalContent ?? readFileIfExists(targetUri);
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

type TargetDocument = {
  document: vscode.TextDocument;
  displayPath: string;
  selection?: vscode.Selection;
};

type PlacementRange = {
  startOffset: number;
  endOffset: number;
};

async function inferTargetDocument(
  option: BenchOption,
  code: string,
  workspaceContext: WorkspaceContext
): Promise<TargetDocument | undefined> {
  const activeEditor = vscode.window.activeTextEditor;
  const declaredSymbols = extractDeclaredSymbols(code);
  const desiredLanguage = workspaceContext.languageId ?? inferLanguageFromCode(code) ?? activeEditor?.document.languageId;

  const candidates: TargetDocument[] = [];
  if (activeEditor) {
    candidates.push({
      document: activeEditor.document,
      displayPath: workspaceContext.activeFileName
        ? vscode.workspace.asRelativePath(workspaceContext.activeFileName, false)
        : path.basename(activeEditor.document.uri.fsPath),
      selection: activeEditor.selection
    });
  }

  const workspaceFiles = await findWorkspaceCandidateFiles(desiredLanguage);
  for (const uri of workspaceFiles) {
    if (activeEditor && uri.toString() === activeEditor.document.uri.toString()) {
      continue;
    }
    const document = await vscode.workspace.openTextDocument(uri);
    candidates.push({
      document,
      displayPath: vscode.workspace.asRelativePath(uri, false)
    });
  }

  const ranked = candidates
    .map((candidate) => ({
      candidate,
      score: scoreTargetDocument(candidate, option, declaredSymbols, desiredLanguage)
    }))
    .sort((left, right) => right.score - left.score);

  return ranked[0]?.candidate;
}

async function findWorkspaceCandidateFiles(languageId?: string): Promise<vscode.Uri[]> {
  const extension = extensionForLanguage(languageId);
  if (!extension) {
    return [];
  }
  return vscode.workspace.findFiles(
    `**/*${extension}`,
    "**/{node_modules,.git,dist,build,__pycache__,.venv,venv}/**",
    40
  );
}

function scoreTargetDocument(
  candidate: TargetDocument,
  option: BenchOption,
  declaredSymbols: string[],
  desiredLanguage?: string
): number {
  const document = candidate.document;
  const text = document.getText();
  const lowerText = text.toLowerCase();
  const lowerPath = candidate.displayPath.toLowerCase();
  let score = 0;

  if (vscode.window.activeTextEditor && document.uri.toString() === vscode.window.activeTextEditor.document.uri.toString()) {
    score += 60;
  }
  if (desiredLanguage && document.languageId === desiredLanguage) {
    score += 20;
  }
  if (/notimplementederror|todo|implement me|bench demo/i.test(text)) {
    score += 25;
  }

  const promptTokens = extractScoringTokens(`${option.title} ${option.summary} ${option.implementationPlan} ${codeSnippetForScoring(option.generatedCode)}`);
  for (const token of promptTokens) {
    if (lowerPath.includes(token)) {
      score += 8;
    }
    if (lowerText.includes(token)) {
      score += 2;
    }
  }

  for (const symbol of declaredSymbols) {
    if (containsDeclaredSymbol(text, document.languageId, symbol)) {
      score += 40;
    }
  }

  return score;
}

function inferPlacementRange(
  text: string,
  languageId: string,
  selection?: { startOffset: number; endOffset: number },
  code?: string
): PlacementRange {
  if (selection) {
    return {
      startOffset: selection.startOffset,
      endOffset: selection.endOffset
    };
  }

  for (const symbol of extractDeclaredSymbols(code ?? "")) {
    const range = findTopLevelSymbolRange(text, languageId, symbol);
    if (range) {
      return range;
    }
  }

  const placeholderRange = findPlaceholderRange(text, languageId);
  if (placeholderRange) {
    return placeholderRange;
  }

  const appendOffset = inferAppendOffset(text, languageId);
  return {
    startOffset: appendOffset,
    endOffset: appendOffset
  };
}

function extractDeclaredSymbols(code: string): string[] {
  const patterns = [
    /\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(/g,
    /\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b/g,
    /\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(/g,
    /\b(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)\b/g,
    /\b(?:export\s+)?interface\s+([A-Za-z_][A-Za-z0-9_]*)\b/g,
    /\b(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s*)?(?:\(|function)/g,
  ];
  const symbols = new Set<string>();
  for (const pattern of patterns) {
    for (const match of code.matchAll(pattern)) {
      if (match[1]) {
        symbols.add(match[1]);
      }
    }
  }
  return [...symbols];
}

function containsDeclaredSymbol(text: string, languageId: string, symbol: string): boolean {
  return Boolean(findTopLevelSymbolRange(text, languageId, symbol));
}

function findTopLevelSymbolRange(text: string, languageId: string, symbol: string): PlacementRange | undefined {
  const escaped = escapeRegExp(symbol);
  const patterns: RegExp[] = languageId === "python"
    ? [new RegExp(`^def\\s+${escaped}\\s*\\(`, "m"), new RegExp(`^class\\s+${escaped}\\b`, "m")]
    : [
        new RegExp(`^(?:export\\s+)?(?:async\\s+)?function\\s+${escaped}\\s*\\(`, "m"),
        new RegExp(`^(?:export\\s+)?class\\s+${escaped}\\b`, "m"),
        new RegExp(`^(?:export\\s+)?interface\\s+${escaped}\\b`, "m"),
        new RegExp(`^(?:export\\s+)?const\\s+${escaped}\\s*=`, "m")
      ];

  for (const pattern of patterns) {
    const match = pattern.exec(text);
    if (!match || match.index === undefined) {
      continue;
    }
    const startOffset = match.index;
    const endOffset = findBlockEnd(text, startOffset, languageId);
    return { startOffset, endOffset };
  }
  return undefined;
}

function findPlaceholderRange(text: string, languageId: string): PlacementRange | undefined {
  const patterns = languageId === "python"
    ? [/^\s*raise\s+NotImplementedError.*$/m, /^\s*pass\s*$/m]
    : [/^\s*\/\/\s*TODO.*$/m, /^\s*throw\s+new\s+Error\(.*implement.*\)\s*;?$/im];
  for (const pattern of patterns) {
    const match = pattern.exec(text);
    if (!match || match.index === undefined) {
      continue;
    }
    const lineStart = text.lastIndexOf("\n", match.index) + 1;
    const lineEndIndex = text.indexOf("\n", match.index + match[0].length);
    const lineEnd = lineEndIndex === -1 ? text.length : lineEndIndex;
    return { startOffset: lineStart, endOffset: lineEnd };
  }
  return undefined;
}

function findBlockEnd(text: string, startOffset: number, languageId: string): number {
  if (languageId === "python") {
    const startLine = text.slice(0, startOffset).split("\n").length - 1;
    const lines = text.split("\n");
    const baseIndent = indentationOf(lines[startLine] ?? "");
    for (let index = startLine + 1; index < lines.length; index += 1) {
      const line = lines[index];
      if (!line.trim()) {
        continue;
      }
      const indent = indentationOf(line);
      if (indent <= baseIndent && /^(def|class)\s+/.test(line.trim())) {
        return offsetForLine(lines, index);
      }
    }
    return text.length;
  }

  const nextMatch = /^(?:export\s+)?(?:(?:async\s+)?function|class|interface|const)\s+/m.exec(text.slice(startOffset + 1));
  if (nextMatch?.index !== undefined) {
    return startOffset + 1 + nextMatch.index;
  }
  return text.length;
}

function inferAppendOffset(text: string, languageId: string): number {
  if (languageId === "python") {
    const importMatches = [...text.matchAll(/^(?:from\s+\S+\s+import\s+.+|import\s+.+)$/gm)];
    if (importMatches.length) {
      const last = importMatches[importMatches.length - 1];
      return text.indexOf("\n", (last.index ?? 0) + last[0].length) + 1;
    }
  } else {
    const importMatches = [...text.matchAll(/^import\s+.+$/gm)];
    if (importMatches.length) {
      const last = importMatches[importMatches.length - 1];
      return text.indexOf("\n", (last.index ?? 0) + last[0].length) + 1;
    }
  }
  return text.length ? `${text}\n`.length - 1 : 0;
}

function indentationOf(line: string): number {
  const match = line.match(/^(\s*)/);
  return match?.[1].length ?? 0;
}

function offsetForLine(lines: string[], index: number): number {
  let total = 0;
  for (let current = 0; current < index; current += 1) {
    total += lines[current].length + 1;
  }
  return total;
}

function extractScoringTokens(text: string): string[] {
  return [...new Set((text.toLowerCase().match(/[a-z]{4,}/g) ?? []).filter((token) => !COMMON_TOKENS.has(token)).slice(0, 18))];
}

function codeSnippetForScoring(code: string): string {
  return code.slice(0, 400);
}

function inferLanguageFromCode(code: string): string | undefined {
  if (/\bdef\s+\w+\s*\(|\bclass\s+\w+\s*:/.test(code)) {
    return "python";
  }
  if (/\bexport\s+|\binterface\s+|\bconst\s+\w+\s*=/.test(code)) {
    return "typescript";
  }
  return undefined;
}

function extensionForLanguage(languageId?: string): string | undefined {
  const map: Record<string, string> = {
    python: ".py",
    typescript: ".ts",
    typescriptreact: ".tsx",
    javascript: ".js",
    javascriptreact: ".jsx",
  };
  return languageId ? map[languageId] : undefined;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const COMMON_TOKENS = new Set([
  "imple", "implement", "implementation", "function", "feature", "option", "return",
  "async", "const", "class", "export", "bench", "draft", "using", "with", "that",
  "from", "this", "into", "then", "they", "have", "your", "will", "code", "plan"
]);
