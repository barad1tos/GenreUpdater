// Mermaid theme configuration for Ayu color scheme
// This runs after page load to configure Mermaid with proper colors

(function() {
  "use strict";

  // Ayu Mirage (dark) theme colors
  const ayuMirage = {
    theme: "base",
    themeVariables: {
      // Background
      background: "#1F2430",
      mainBkg: "#272D38",
      secondBkg: "#1F2430",

      // Text
      primaryTextColor: "#CBCCC6",
      secondaryTextColor: "#707A8C",
      tertiaryTextColor: "#5C6773",

      // Primary colors (cyan)
      primaryColor: "#73D0FF",
      primaryBorderColor: "#5C6773",

      // Secondary colors (yellow)
      secondaryColor: "#FFCC66",
      secondaryBorderColor: "#5C6773",

      // Tertiary colors (green)
      tertiaryColor: "#BAE67E",
      tertiaryBorderColor: "#5C6773",

      // Lines and borders
      lineColor: "#707A8C",
      textColor: "#CBCCC6",

      // Notes
      noteBkgColor: "#272D38",
      noteTextColor: "#CBCCC6",
      noteBorderColor: "#3D424D",

      // Flowchart specific
      nodeBkg: "#73D0FF",
      nodeTextColor: "#1F2430",
      nodeBorder: "#5C6773",
      clusterBkg: "#272D38",
      clusterBorder: "#3D424D",
      defaultLinkColor: "#707A8C",
      edgeLabelBackground: "#1F2430",

      // Sequence diagram
      actorBkg: "#FFD580",
      actorTextColor: "#1F2430",
      actorBorder: "#5C6773",
      actorLineColor: "#5C6773",
      signalColor: "#707A8C",
      signalTextColor: "#CBCCC6",
      activationBkgColor: "#3D424D",
      activationBorderColor: "#5C6773",

      // Class diagram
      classText: "#CBCCC6",

      // State diagram
      labelColor: "#CBCCC6",

      // Fonts
      fontFamily: '"Roboto", sans-serif',
      fontSize: "14px"
    }
  };

  // Ayu Light theme colors
  const ayuLight = {
    theme: "base",
    themeVariables: {
      // Background
      background: "#FAFAFA",
      mainBkg: "#FFFFFF",
      secondBkg: "#F3F4F5",

      // Text
      primaryTextColor: "#575F66",
      secondaryTextColor: "#8A9199",
      tertiaryTextColor: "#ABB0B6",

      // Primary colors (blue)
      primaryColor: "#E8F4FD",
      primaryBorderColor: "#399EE6",

      // Secondary colors (orange)
      secondaryColor: "#FFF3E0",
      secondaryBorderColor: "#FF9940",

      // Tertiary colors (green)
      tertiaryColor: "#E8F5E9",
      tertiaryBorderColor: "#86B300",

      // Lines and borders
      lineColor: "#8A9199",
      textColor: "#575F66",

      // Notes
      noteBkgColor: "#F3F4F5",
      noteTextColor: "#575F66",
      noteBorderColor: "#E8E9EB",

      // Flowchart specific
      nodeBkg: "#E8F4FD",
      nodeTextColor: "#575F66",
      nodeBorder: "#399EE6",
      clusterBkg: "#F3F4F5",
      clusterBorder: "#E8E9EB",
      defaultLinkColor: "#8A9199",
      edgeLabelBackground: "#FAFAFA",

      // Sequence diagram
      actorBkg: "#FFF3E0",
      actorTextColor: "#575F66",
      actorBorder: "#FF9940",
      actorLineColor: "#8A9199",
      signalColor: "#8A9199",
      signalTextColor: "#575F66",
      activationBkgColor: "#F3F4F5",
      activationBorderColor: "#E8E9EB",

      // Class diagram
      classText: "#575F66",

      // State diagram
      labelColor: "#575F66",

      // Fonts
      fontFamily: '"Roboto", sans-serif',
      fontSize: "14px"
    }
  };

  // Function to get current theme
  const getCurrentTheme = function() {
    const scheme = document.body.getAttribute("data-md-color-scheme");
    return scheme === "slate" ? ayuMirage : ayuLight;
  };

  // Initialize Mermaid with current theme
  const initMermaid = function() {
    if (typeof mermaid === "undefined") {
      return;
    }

    const config = getCurrentTheme();
    mermaid.initialize({
      startOnLoad: false,
      theme: config.theme,
      themeVariables: config.themeVariables
      // Using default securityLevel (strict) for XSS protection
    });

    // Re-render all mermaid diagrams safely using textContent
    const diagrams = document.querySelectorAll(".mermaid");
    diagrams.forEach(function(el) {
      const code = el.textContent;
      el.removeAttribute("data-processed");
      // Clear element safely and set text content
      while (el.firstChild) {
        el.removeChild(el.firstChild);
      }
      el.textContent = code;
    });

    mermaid.init(undefined, ".mermaid");
  };

  // Wait for mermaid to be available with polling
  const waitForMermaid = function(callback, maxAttempts) {
    let attempts = 0;
    const checkInterval = 100; // ms

    const check = function() {
      attempts++;
      if (typeof mermaid !== "undefined") {
        callback();
      } else if (attempts < maxAttempts) {
        setTimeout(check, checkInterval);
      }
      // Stop silently after max attempts - mermaid not available
    };

    check();
  };

  // Setup theme observer
  const setupThemeObserver = function() {
    const observer = new MutationObserver(function(mutations) {
      mutations.forEach(function(mutation) {
        if (mutation.attributeName === "data-md-color-scheme") {
          initMermaid();
        }
      });
    });

    observer.observe(document.body, {
      attributes: true,
      attributeFilter: ["data-md-color-scheme"]
    });
  };

  // Initialize when DOM is ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function() {
      // Poll for mermaid availability (max 50 attempts = 5 seconds)
      waitForMermaid(function() {
        initMermaid();
        setupThemeObserver();
      }, 50);
    });
  } else {
    // DOM already loaded
    waitForMermaid(function() {
      initMermaid();
      setupThemeObserver();
    }, 50);
  }
})();
