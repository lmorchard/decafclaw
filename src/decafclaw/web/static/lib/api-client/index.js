// src/decafclaw/web/static/lib/api-client/core/ApiError.ts
var ApiError = class extends Error {
  url;
  status;
  statusText;
  body;
  request;
  constructor(request3, response, message) {
    super(message);
    this.name = "ApiError";
    this.url = response.url;
    this.status = response.status;
    this.statusText = response.statusText;
    this.body = response.body;
    this.request = request3;
  }
};

// src/decafclaw/web/static/lib/api-client/core/CancelablePromise.ts
var CancelError = class extends Error {
  constructor(message) {
    super(message);
    this.name = "CancelError";
  }
  get isCancelled() {
    return true;
  }
};
var CancelablePromise = class {
  #isResolved;
  #isRejected;
  #isCancelled;
  #cancelHandlers;
  #promise;
  #resolve;
  #reject;
  constructor(executor) {
    this.#isResolved = false;
    this.#isRejected = false;
    this.#isCancelled = false;
    this.#cancelHandlers = [];
    this.#promise = new Promise((resolve2, reject) => {
      this.#resolve = resolve2;
      this.#reject = reject;
      const onResolve = (value) => {
        if (this.#isResolved || this.#isRejected || this.#isCancelled) {
          return;
        }
        this.#isResolved = true;
        if (this.#resolve) this.#resolve(value);
      };
      const onReject = (reason) => {
        if (this.#isResolved || this.#isRejected || this.#isCancelled) {
          return;
        }
        this.#isRejected = true;
        if (this.#reject) this.#reject(reason);
      };
      const onCancel = (cancelHandler) => {
        if (this.#isResolved || this.#isRejected || this.#isCancelled) {
          return;
        }
        this.#cancelHandlers.push(cancelHandler);
      };
      Object.defineProperty(onCancel, "isResolved", {
        get: () => this.#isResolved
      });
      Object.defineProperty(onCancel, "isRejected", {
        get: () => this.#isRejected
      });
      Object.defineProperty(onCancel, "isCancelled", {
        get: () => this.#isCancelled
      });
      return executor(onResolve, onReject, onCancel);
    });
  }
  get [Symbol.toStringTag]() {
    return "Cancellable Promise";
  }
  then(onFulfilled, onRejected) {
    return this.#promise.then(onFulfilled, onRejected);
  }
  catch(onRejected) {
    return this.#promise.catch(onRejected);
  }
  finally(onFinally) {
    return this.#promise.finally(onFinally);
  }
  cancel() {
    if (this.#isResolved || this.#isRejected || this.#isCancelled) {
      return;
    }
    this.#isCancelled = true;
    if (this.#cancelHandlers.length) {
      try {
        for (const cancelHandler of this.#cancelHandlers) {
          cancelHandler();
        }
      } catch (error) {
        console.warn("Cancellation threw an error", error);
        return;
      }
    }
    this.#cancelHandlers.length = 0;
    if (this.#reject) this.#reject(new CancelError("Request aborted"));
  }
  get isCancelled() {
    return this.#isCancelled;
  }
};

// src/decafclaw/web/static/lib/api-client/core/OpenAPI.ts
var OpenAPI = {
  BASE: "",
  VERSION: "0.1.0",
  WITH_CREDENTIALS: false,
  CREDENTIALS: "include",
  TOKEN: void 0,
  USERNAME: void 0,
  PASSWORD: void 0,
  HEADERS: void 0,
  ENCODE_PATH: void 0
};

// src/decafclaw/web/static/lib/api-client/models/WorkspaceFileEntry.ts
var WorkspaceFileEntry;
((WorkspaceFileEntry2) => {
  let kind;
  ((kind2) => {
    kind2["TEXT"] = "text";
    kind2["IMAGE"] = "image";
    kind2["BINARY"] = "binary";
  })(kind = WorkspaceFileEntry2.kind || (WorkspaceFileEntry2.kind = {}));
})(WorkspaceFileEntry || (WorkspaceFileEntry = {}));

// src/decafclaw/web/static/lib/api-client/core/generated-request.ts
var isDefined = (value) => {
  return value !== void 0 && value !== null;
};
var isString = (value) => {
  return typeof value === "string";
};
var isStringWithValue = (value) => {
  return isString(value) && value !== "";
};
var isBlob = (value) => {
  return typeof value === "object" && typeof value.type === "string" && typeof value.stream === "function" && typeof value.arrayBuffer === "function" && typeof value.constructor === "function" && typeof value.constructor.name === "string" && /^(Blob|File)$/.test(value.constructor.name) && /^(Blob|File)$/.test(value[Symbol.toStringTag]);
};
var isFormData = (value) => {
  return value instanceof FormData;
};
var base64 = (str) => {
  try {
    return btoa(str);
  } catch (err) {
    return Buffer.from(str).toString("base64");
  }
};
var getQueryString = (params) => {
  const qs = [];
  const append = (key, value) => {
    qs.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  };
  const process = (key, value) => {
    if (isDefined(value)) {
      if (Array.isArray(value)) {
        value.forEach((v) => {
          process(key, v);
        });
      } else if (typeof value === "object") {
        Object.entries(value).forEach(([k, v]) => {
          process(`${key}[${k}]`, v);
        });
      } else {
        append(key, value);
      }
    }
  };
  Object.entries(params).forEach(([key, value]) => {
    process(key, value);
  });
  if (qs.length > 0) {
    return `?${qs.join("&")}`;
  }
  return "";
};
var getUrl = (config, options) => {
  const encoder = config.ENCODE_PATH || encodeURI;
  const path = options.url.replace("{api-version}", config.VERSION).replace(/{(.*?)}/g, (substring, group) => {
    if (options.path?.hasOwnProperty(group)) {
      return encoder(String(options.path[group]));
    }
    return substring;
  });
  const url = `${config.BASE}${path}`;
  if (options.query) {
    return `${url}${getQueryString(options.query)}`;
  }
  return url;
};
var getFormData = (options) => {
  if (options.formData) {
    const formData = new FormData();
    const process = (key, value) => {
      if (isString(value) || isBlob(value)) {
        formData.append(key, value);
      } else {
        formData.append(key, JSON.stringify(value));
      }
    };
    Object.entries(options.formData).filter(([_, value]) => isDefined(value)).forEach(([key, value]) => {
      if (Array.isArray(value)) {
        value.forEach((v) => process(key, v));
      } else {
        process(key, value);
      }
    });
    return formData;
  }
  return void 0;
};
var resolve = async (options, resolver) => {
  if (typeof resolver === "function") {
    return resolver(options);
  }
  return resolver;
};
var getHeaders = async (config, options) => {
  const [token, username, password, additionalHeaders] = await Promise.all([
    resolve(options, config.TOKEN),
    resolve(options, config.USERNAME),
    resolve(options, config.PASSWORD),
    resolve(options, config.HEADERS)
  ]);
  const headers = Object.entries({
    Accept: "application/json",
    ...additionalHeaders,
    ...options.headers
  }).filter(([_, value]) => isDefined(value)).reduce((headers2, [key, value]) => ({
    ...headers2,
    [key]: String(value)
  }), {});
  if (isStringWithValue(token)) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (isStringWithValue(username) && isStringWithValue(password)) {
    const credentials = base64(`${username}:${password}`);
    headers["Authorization"] = `Basic ${credentials}`;
  }
  if (options.body !== void 0) {
    if (options.mediaType) {
      headers["Content-Type"] = options.mediaType;
    } else if (isBlob(options.body)) {
      headers["Content-Type"] = options.body.type || "application/octet-stream";
    } else if (isString(options.body)) {
      headers["Content-Type"] = "text/plain";
    } else if (!isFormData(options.body)) {
      headers["Content-Type"] = "application/json";
    }
  }
  return new Headers(headers);
};
var getRequestBody = (options) => {
  if (options.body !== void 0) {
    if (options.mediaType?.includes("/json")) {
      return JSON.stringify(options.body);
    } else if (isString(options.body) || isBlob(options.body) || isFormData(options.body)) {
      return options.body;
    } else {
      return JSON.stringify(options.body);
    }
  }
  return void 0;
};
var sendRequest = async (config, options, url, body, formData, headers, onCancel) => {
  const controller = new AbortController();
  const request3 = {
    headers,
    body: body ?? formData,
    method: options.method,
    signal: controller.signal
  };
  if (config.WITH_CREDENTIALS) {
    request3.credentials = config.CREDENTIALS;
  }
  onCancel(() => controller.abort());
  return await fetch(url, request3);
};
var getResponseHeader = (response, responseHeader) => {
  if (responseHeader) {
    const content = response.headers.get(responseHeader);
    if (isString(content)) {
      return content;
    }
  }
  return void 0;
};
var getResponseBody = async (response) => {
  if (response.status !== 204) {
    try {
      const contentType = response.headers.get("Content-Type");
      if (contentType) {
        const jsonTypes = ["application/json", "application/problem+json"];
        const isJSON = jsonTypes.some((type) => contentType.toLowerCase().startsWith(type));
        if (isJSON) {
          return await response.json();
        } else {
          return await response.text();
        }
      }
    } catch (error) {
      console.error(error);
    }
  }
  return void 0;
};
var catchErrorCodes = (options, result) => {
  const errors = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    ...options.errors
  };
  const error = errors[result.status];
  if (error) {
    throw new ApiError(options, result, error);
  }
  if (!result.ok) {
    const errorStatus = result.status ?? "unknown";
    const errorStatusText = result.statusText ?? "unknown";
    const errorBody = (() => {
      try {
        return JSON.stringify(result.body, null, 2);
      } catch (e) {
        return void 0;
      }
    })();
    throw new ApiError(
      options,
      result,
      `Generic Error: status: ${errorStatus}; status text: ${errorStatusText}; body: ${errorBody}`
    );
  }
};
var request = (config, options) => {
  return new CancelablePromise(async (resolve2, reject, onCancel) => {
    try {
      const url = getUrl(config, options);
      const formData = getFormData(options);
      const body = getRequestBody(options);
      const headers = await getHeaders(config, options);
      if (!onCancel.isCancelled) {
        const response = await sendRequest(config, options, url, body, formData, headers, onCancel);
        const responseBody = await getResponseBody(response);
        const responseHeader = getResponseHeader(response, options.responseHeader);
        const result = {
          url,
          ok: response.ok,
          status: response.status,
          statusText: response.statusText,
          body: responseHeader ?? responseBody
        };
        catchErrorCodes(options, result);
        resolve2(result.body);
      }
    } catch (error) {
      reject(error);
    }
  });
};

// src/decafclaw/web/static/lib/api-client/core/request.ts
var request2 = (config, options) => {
  const notificationRead = options.method === "POST" && ["/api/notifications/{id}/read", "/api/notifications/read-all"].includes(options.url);
  const notification = notificationRead || options.method === "GET" && ["/api/notifications", "/api/notifications/unread-count"].includes(options.url);
  const login = options.method === "POST" && options.url === "/api/auth/login";
  const logout = options.method === "POST" && options.url === "/api/auth/logout";
  const vaultGuard = options.method === "GET" && options.url === "/api/auth/me" && options.discardResponse;
  const diagnostics = options.method === "GET" && options.url === "/api/conversations/{id}/context";
  const sticky = options.url === "/api/sticky/{conv_id}";
  const widgetCatalog = options.method === "GET" && options.url === "/api/widgets";
  const canvasState = options.method === "GET" && options.url === "/api/canvas/{conv_id}";
  const canvasNewTab = options.method === "POST" && options.url === "/api/canvas/{conv_id}/new_tab";
  const canvasIgnoredMutation = options.method === "POST" && ["/api/canvas/{conv_id}/active_tab", "/api/canvas/{conv_id}/close_tab"].includes(options.url);
  const canvas = canvasState || canvasNewTab || canvasIgnoredMutation;
  const workspaceRead = options.method === "GET" && ["/api/workspace", "/api/workspace/recent", "/api/workspace-file/{path}", "/api/autocomplete"].includes(options.url);
  const listing = ["/api/conversations", "/api/conversations/archived", "/api/conversations/system"].includes(options.url);
  const patch = options.method === "PATCH" && options.url === "/api/conversations/{id}";
  const create = options.method === "POST" && options.url === "/api/conversations";
  const lifecycle = options.method === "DELETE" && options.url === "/api/conversations/{id}" || options.method === "POST" && ["/api/conversations/{id}/archive", "/api/conversations/{id}/unarchive"].includes(options.url);
  const folder = options.method === "POST" && options.url === "/api/conversations/folders" || ["PUT", "DELETE"].includes(options.method) && options.url === "/api/conversations/folders/{path}";
  if (!notification && !diagnostics && !login && !logout && !vaultGuard && !create && !lifecycle && !folder && !patch && !widgetCatalog && !canvas && !workspaceRead && (options.method !== "GET" || !sticky && !listing)) {
    return request(config, options);
  }
  return new CancelablePromise(async (resolve2, reject, onCancel) => {
    try {
      const query = listing && options.query?.folder === "" ? void 0 : options.query;
      const url = workspaceRead && options.url === "/api/workspace-file/{path}" ? `${config.BASE}/api/workspace-file/${String(options.path.path).split("/").map(encodeURIComponent).join("/")}` : workspaceRead ? `${config.BASE}${options.url}${options.query ? getQueryString(options.query) : ""}` : folder ? `${config.BASE}/api/conversations/folders${options.path ? "/" + String(options.path.path).split("/").map(encodeURIComponent).join("/") : ""}` : canvas ? `${config.BASE}${options.url.replace("{conv_id}", encodeURIComponent(options.path.conv_id))}` : patch || lifecycle || diagnostics || notificationRead && options.path ? `${config.BASE}${options.url.replace("{id}", encodeURIComponent(options.path.id))}${query ? getQueryString(query) : ""}` : sticky ? `${config.BASE}/api/sticky/${encodeURIComponent(options.path.conv_id)}` : `${config.BASE}${options.url}${query ? getQueryString(query) : ""}`;
      const headers = await getHeaders(config, options);
      if (onCancel.isCancelled) return;
      const response = await sendRequest(
        {
          ...config,
          WITH_CREDENTIALS: true,
          CREDENTIALS: widgetCatalog ? "include" : "same-origin"
        },
        options,
        url,
        getRequestBody(options),
        void 0,
        headers,
        onCancel
      );
      if (!((logout || notificationRead || canvasIgnoredMutation) && options.discardResponse)) catchErrorCodes(options, {
        url,
        ok: response.ok,
        status: response.status,
        statusText: response.statusText,
        body: folder && !response.ok ? await response.json() : canvasNewTab && !response.ok ? await response.text() : void 0
      });
      if (options.discardResponse) {
        resolve2(void 0);
      } else {
        resolve2(await response.json());
      }
    } catch (error) {
      reject(error);
    }
  });
};
var textRequest = (config, options) => {
  return new CancelablePromise(async (resolve2, reject, onCancel) => {
    try {
      const url = `${config.BASE}${options.url.replace("{id}", encodeURIComponent(options.path.id))}${options.query ? getQueryString(options.query) : ""}`;
      const headers = await getHeaders(config, options);
      if (onCancel.isCancelled) return;
      const response = await sendRequest(
        { ...config, WITH_CREDENTIALS: true, CREDENTIALS: "same-origin" },
        options,
        url,
        void 0,
        void 0,
        headers,
        onCancel
      );
      catchErrorCodes(options, {
        url,
        ok: response.ok,
        status: response.status,
        statusText: response.statusText,
        body: void 0
      });
      resolve2(await response.text());
    } catch (error) {
      reject(error);
    }
  });
};

// src/decafclaw/web/static/lib/api-client/services/DefaultService.ts
var DefaultService = class {
  /**
   * Health
   * Liveness probe — returns the static health snapshot.
   * @returns any Successful Response
   * @throws ApiError
   */
  static healthHealthGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/health"
    });
  }
  /**
   * Metrics Endpoint
   * Prometheus text format metrics endpoint.
   * @returns any Successful Response
   * @throws ApiError
   */
  static metricsEndpointMetricsGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/metrics"
    });
  }
  /**
   * Handle Confirm
   * Handle Mattermost interactive button callbacks for tool confirmation.
   * @returns any Successful Response
   * @throws ApiError
   */
  static handleConfirmActionsConfirmPost() {
    return request2(OpenAPI, {
      method: "POST",
      url: "/actions/confirm"
    });
  }
  /**
   * Handle Cancel
   * Handle Mattermost interactive button callback for stop/cancel.
   * @returns any Successful Response
   * @throws ApiError
   */
  static handleCancelActionsCancelPost() {
    return request2(OpenAPI, {
      method: "POST",
      url: "/actions/cancel"
    });
  }
  /**
   * Auth Login
   * Validate a one-time login token, then set the session cookie.
   * @param requestBody
   * @returns LoginResponse Successful Response
   * @throws ApiError
   */
  static authLoginApiAuthLoginPost(requestBody) {
    return request2(OpenAPI, {
      method: "POST",
      url: "/api/auth/login",
      body: requestBody,
      mediaType: "application/json"
    });
  }
  static authLogoutApiAuthLogoutPost(discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "POST",
      url: "/api/auth/logout"
    });
  }
  static authMeApiAuthMeGet(discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "GET",
      url: "/api/auth/me"
    });
  }
  /**
   * List Conversations
   * List conversations and subfolders for a specific folder.
   *
   * Query params:
   * folder — folder path (default: top-level)
   *
   * Returns ``{folder, folders, conversations}`` mirroring vault_list pattern.
   * @param folder
   * @returns ConversationListingResponse Successful Response
   * @throws ApiError
   */
  static listConversationsApiConversationsGet(folder = "") {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/conversations",
      query: {
        "folder": folder
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  /**
   * Create Conversation
   * Create a new conversation, optionally in a folder with a model.
   * @param requestBody
   * @returns ConversationCreateResponse Successful Response
   * @throws ApiError
   */
  static createConversationApiConversationsPost(requestBody) {
    return request2(OpenAPI, {
      method: "POST",
      url: "/api/conversations",
      body: requestBody,
      mediaType: "application/json"
    });
  }
  /**
   * List Archived Conversations
   * List archived conversations, optionally filtered by folder.
   * @param folder
   * @returns ConversationListingResponse Successful Response
   * @throws ApiError
   */
  static listArchivedConversationsApiConversationsArchivedGet(folder = "") {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/conversations/archived",
      query: {
        "folder": folder
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  /**
   * List System Conversations
   * List system conversations, grouped by type sub-folders.
   * @param folder
   * @returns SystemConversationListingResponse Successful Response
   * @throws ApiError
   */
  static listSystemConversationsApiConversationsSystemGet(folder = "") {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/conversations/system",
      query: {
        "folder": folder
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiConversationsIdGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/conversations/{id}"
    });
  }
  static renameConversationApiConversationsIdPatch(id, requestBody, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "PATCH",
      url: "/api/conversations/{id}",
      path: {
        "id": id
      },
      body: requestBody,
      mediaType: "application/json",
      errors: {
        422: `Validation Error`
      }
    });
  }
  static deleteConversationApiConversationsIdDelete(id, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "DELETE",
      url: "/api/conversations/{id}",
      path: {
        "id": id
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiConversationsIdHistoryGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/conversations/{id}/history"
    });
  }
  /**
   * Get Context Diagnostics
   * Return context composer diagnostics for a conversation.
   * @param id
   * @returns ContextDiagnosticsResponse Successful Response
   * @throws ApiError
   */
  static getContextDiagnosticsApiConversationsIdContextGet(id) {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/conversations/{id}/context",
      path: {
        "id": id
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  /**
   * Export Conversation
   * Export a conversation as raw JSONL or rendered markdown.
   *
   * Query param ``format`` must be ``jsonl`` or ``markdown``. 400 on missing
   * or unknown format, 404 if the conversation isn't owned by the user or
   * no archive exists.
   * @param id
   * @param format
   * @returns string Successful Response
   * @throws ApiError
   */
  static exportConversationApiConversationsIdExportGet(id, format) {
    return textRequest(OpenAPI, {
      method: "GET",
      url: "/api/conversations/{id}/export",
      path: {
        "id": id
      },
      query: {
        "format": format
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  static createConvFolderApiConversationsFoldersPost(requestBody, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "POST",
      url: "/api/conversations/folders",
      body: requestBody,
      mediaType: "application/json"
    });
  }
  static deleteConvFolderApiConversationsFoldersPathDelete(path, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "DELETE",
      url: "/api/conversations/folders/{path}",
      path: {
        "path": path
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  static renameConvFolderApiConversationsFoldersPathPut(path, requestBody, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "PUT",
      url: "/api/conversations/folders/{path}",
      path: {
        "path": path
      },
      body: requestBody,
      mediaType: "application/json",
      errors: {
        422: `Validation Error`
      }
    });
  }
  static archiveConversationApiConversationsIdArchivePost(id, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "POST",
      url: "/api/conversations/{id}/archive",
      path: {
        "id": id
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  static unarchiveConversationApiConversationsIdUnarchivePost(id, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "POST",
      url: "/api/conversations/{id}/unarchive",
      path: {
        "id": id
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  /**
   * List Notifications
   * Return inbox records newest first, with a joined ``read`` bool.
   * @param limit
   * @param before
   * @returns NotificationListResponse Successful Response
   * @throws ApiError
   */
  static listNotificationsApiNotificationsGet(limit = 20, before) {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/notifications",
      query: {
        "limit": limit,
        "before": before
      }
    });
  }
  /**
   * Notifications Unread Count
   * Return ``{"count": N}`` — called frequently, stays cheap.
   * @returns NotificationCountResponse Successful Response
   * @throws ApiError
   */
  static notificationsUnreadCountApiNotificationsUnreadCountGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/notifications/unread-count"
    });
  }
  static notificationsMarkAllReadApiNotificationsReadAllPost(discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "POST",
      url: "/api/notifications/read-all"
    });
  }
  static notificationsMarkReadApiNotificationsIdReadPost(id, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "POST",
      url: "/api/notifications/{id}/read",
      path: {
        "id": id
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiUploadConvIdPost() {
    return request2(OpenAPI, {
      method: "POST",
      url: "/api/upload/{conv_id}"
    });
  }
  /**
   * Wrapper
   * @param folder
   * @returns WorkspaceListingResponse Successful Response
   * @throws ApiError
   */
  static wrapperApiWorkspaceGet(folder) {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/workspace",
      query: {
        "folder": folder
      }
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiWorkspacePost() {
    return request2(OpenAPI, {
      method: "POST",
      url: "/api/workspace"
    });
  }
  /**
   * Wrapper
   * @returns WorkspaceRecentResponse Successful Response
   * @throws ApiError
   */
  static wrapperApiWorkspaceRecentGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/workspace/recent"
    });
  }
  /**
   * Wrapper
   * @param q
   * @returns AutocompleteResponse Successful Response
   * @throws ApiError
   */
  static wrapperApiAutocompleteGet(q) {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/autocomplete",
      query: {
        "q": q
      }
    });
  }
  /**
   * Wrapper
   * @param path
   * @returns WorkspaceTextResponse Successful Response
   * @throws ApiError
   */
  static wrapperApiWorkspaceFilePathGet(path) {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/workspace-file/{path}",
      path: {
        "path": path
      }
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiWorkspacePathGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/workspace/{path}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiWorkspacePathPut() {
    return request2(OpenAPI, {
      method: "PUT",
      url: "/api/workspace/{path}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiWorkspacePathDelete() {
    return request2(OpenAPI, {
      method: "DELETE",
      url: "/api/workspace/{path}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiConfigFilesGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/config/files"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiConfigFilesPathGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/config/files/{path}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiConfigFilesPathPut() {
    return request2(OpenAPI, {
      method: "PUT",
      url: "/api/config/files/{path}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiModelsGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/models"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiSchedulesGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/schedules"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiSchedulesNameRunPost() {
    return request2(OpenAPI, {
      method: "POST",
      url: "/api/schedules/{name}/run"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiSchedulesNameOverlayDelete() {
    return request2(OpenAPI, {
      method: "DELETE",
      url: "/api/schedules/{name}/overlay"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiSchedulesNameGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/schedules/{name}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiSchedulesNamePut() {
    return request2(OpenAPI, {
      method: "PUT",
      url: "/api/schedules/{name}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiVaultGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/vault"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiVaultPost() {
    return request2(OpenAPI, {
      method: "POST",
      url: "/api/vault"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiVaultFoldersPost() {
    return request2(OpenAPI, {
      method: "POST",
      url: "/api/vault/folders"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiVaultRecentGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/vault/recent"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiVaultTagsGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/vault/tags"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiVaultPageGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/vault/{page}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiVaultPagePut() {
    return request2(OpenAPI, {
      method: "PUT",
      url: "/api/vault/{page}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiVaultPageDelete() {
    return request2(OpenAPI, {
      method: "DELETE",
      url: "/api/vault/{page}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperVaultPageGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/vault/{page}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiWikiGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/wiki"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperApiWikiPageGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/wiki/{page}"
    });
  }
  /**
   * List Widgets
   * Return the widget catalog with cache-busted js URLs.
   * @returns WidgetCatalogResponse Successful Response
   * @throws ApiError
   */
  static listWidgetsApiWidgetsGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/widgets"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperWidgetsTierNameWidgetJsGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/widgets/{tier}/{name}/widget.js"
    });
  }
  /**
   * Get Canvas State
   * Load current canvas state for a conversation.
   * @param convId
   * @returns CanvasStateResponse Successful Response
   * @throws ApiError
   */
  static getCanvasStateApiCanvasConvIdGet(convId) {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/canvas/{conv_id}",
      path: {
        "conv_id": convId
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  /**
   * Get Sticky State
   * Load current sticky-slot state for a conversation (reload recovery).
   * @param convId
   * @returns StickyResponse Successful Response
   * @throws ApiError
   */
  static getStickyStateApiStickyConvIdGet(convId) {
    return request2(OpenAPI, {
      method: "GET",
      url: "/api/sticky/{conv_id}",
      path: {
        "conv_id": convId
      },
      errors: {
        422: `Validation Error`
      }
    });
  }
  static postCanvasNewTabApiCanvasConvIdNewTabPost(convId, requestBody, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "POST",
      url: "/api/canvas/{conv_id}/new_tab",
      path: {
        "conv_id": convId
      },
      body: requestBody,
      mediaType: "application/json",
      errors: {
        422: `Validation Error`
      }
    });
  }
  static postCanvasActiveTabApiCanvasConvIdActiveTabPost(convId, requestBody, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "POST",
      url: "/api/canvas/{conv_id}/active_tab",
      path: {
        "conv_id": convId
      },
      body: requestBody,
      mediaType: "application/json",
      errors: {
        422: `Validation Error`
      }
    });
  }
  static postCanvasCloseTabApiCanvasConvIdCloseTabPost(convId, requestBody, discardResponse = false) {
    return request2(OpenAPI, {
      discardResponse,
      method: "POST",
      url: "/api/canvas/{conv_id}/close_tab",
      path: {
        "conv_id": convId
      },
      body: requestBody,
      mediaType: "application/json",
      errors: {
        422: `Validation Error`
      }
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperCanvasConvIdGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/canvas/{conv_id}"
    });
  }
  /**
   * Wrapper
   * @returns any Successful Response
   * @throws ApiError
   */
  static wrapperCanvasConvIdTabIdGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/canvas/{conv_id}/{tab_id}"
    });
  }
  /**
   * Serve Index
   * @returns any Successful Response
   * @throws ApiError
   */
  static serveIndexGet() {
    return request2(OpenAPI, {
      method: "GET",
      url: "/"
    });
  }
};
export {
  ApiError,
  CancelError,
  CancelablePromise,
  DefaultService,
  OpenAPI,
  WorkspaceFileEntry
};
