// Barrel re-export — types and API calls are now split into sub-modules for
// maintainability. All existing imports (`from "../../api/knowledge"`) keep
// working unchanged; only the internal organisation has changed.
export * from "./knowledge/types";
export * from "./knowledge/api";
