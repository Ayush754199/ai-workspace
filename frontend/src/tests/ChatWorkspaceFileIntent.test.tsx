import { describe, expect, it } from "vitest";
import { requestedFileFormat } from "../pages/ChatWorkspace";

// The user may type in English, Hinglish, romanised Hindi or Devanagari, so the
// detector must only depend on the format name (PDF/excel/xlsx...) plus any kind
// of "make it" intent, never on one particular verb list.
describe("requestedFileFormat", () => {
  it.each([
    ["eska pdf bana do", "pdf"],
    ["bhai report banao pdf me", "pdf"],
    ["is data ka excel banao", "xlsx"],
    ["spreadsheet chahiye", "xlsx"],
    ["file banao word me", "docx"],
    ["csv file bana do", "csv"],
    ["presentation banao pptx", "pptx"],
    ["isko pdf make karo", "pdf"],
    // कोई फॉर्मेट नहीं बताया गया — फिर भी कोड नहीं, असली फाइल बननी चाहिए (docx डिफ़ॉल्ट)।
    ["डाउनलोड करने लायक फाइल बनाओ", "docx"],
    ["ek file bana do", "docx"],
    ["इसे PDF बना के दो", "pdf"],
    ["invoice.xlsx download karo", "xlsx"],
  ])("detects %s as %s", (prompt, expected) => {
    expect(requestedFileFormat(prompt)).toBe(expected);
  });

  it.each([
    "excel me vlookup kaise use karein",
    "what is a pdf file",
    "how do I create a csv in python",
    "Write a python function to read a file",
  ])("leaves plain questions alone: %s", (prompt) => {
    expect(requestedFileFormat(prompt)).toBeNull();
  });
});
