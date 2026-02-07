/**
 * Client-side tool implementations.
 *
 * These tools execute in the browser and their results are sent back
 * to the backend to continue the agent conversation.
 */

export interface ClientToolResult {
  success: boolean
  result: string
  error?: string
}

export type ClientToolExecutor = (
  arguments: Record<string, unknown>
) => Promise<ClientToolResult>

/**
 * Read the text content of the current page.
 */
async function readPage(): Promise<ClientToolResult> {
  try {
    const content = document.body.innerText || document.body.textContent || ''
    return {
      success: true,
      result: content.slice(0, 50000), // Limit to 50k chars
    }
  } catch (error) {
    return {
      success: false,
      result: '',
      error: `Failed to read page: ${error}`,
    }
  }
}

/**
 * Get the HTML of an element by CSS selector.
 */
async function getElement(
  arguments_: Record<string, unknown>
): Promise<ClientToolResult> {
  try {
    const selector = arguments_.selector as string
    if (!selector) {
      return {
        success: false,
        result: '',
        error: 'Missing required argument: selector',
      }
    }

    const element = document.querySelector(selector)
    if (!element) {
      return {
        success: false,
        result: '',
        error: `No element found for selector: ${selector}`,
      }
    }

    return {
      success: true,
      result: element.outerHTML.slice(0, 10000), // Limit size
    }
  } catch (error) {
    return {
      success: false,
      result: '',
      error: `Failed to get element: ${error}`,
    }
  }
}

/**
 * Execute an action like scroll, click, or highlight.
 */
async function executeAction(
  arguments_: Record<string, unknown>
): Promise<ClientToolResult> {
  try {
    const action = arguments_.action as string
    const target = arguments_.target as string | undefined

    switch (action) {
      case 'scroll': {
        const direction = arguments_.direction as string || 'down'
        const amount = (arguments_.amount as number) || 300

        if (direction === 'down') {
          window.scrollBy(0, amount)
        } else if (direction === 'up') {
          window.scrollBy(0, -amount)
        } else if (direction === 'top') {
          window.scrollTo(0, 0)
        } else if (direction === 'bottom') {
          window.scrollTo(0, document.body.scrollHeight)
        }

        return {
          success: true,
          result: `Scrolled ${direction}`,
        }
      }

      case 'click': {
        if (!target) {
          return {
            success: false,
            result: '',
            error: 'Missing target selector for click action',
          }
        }

        const element = document.querySelector(target) as HTMLElement
        if (!element) {
          return {
            success: false,
            result: '',
            error: `No element found for selector: ${target}`,
          }
        }

        element.click()
        return {
          success: true,
          result: `Clicked element: ${target}`,
        }
      }

      case 'highlight': {
        if (!target) {
          return {
            success: false,
            result: '',
            error: 'Missing target selector for highlight action',
          }
        }

        const element = document.querySelector(target) as HTMLElement
        if (!element) {
          return {
            success: false,
            result: '',
            error: `No element found for selector: ${target}`,
          }
        }

        // Save original style
        const originalOutline = element.style.outline
        const originalBackground = element.style.backgroundColor

        // Apply highlight
        element.style.outline = '3px solid #ff6b00'
        element.style.backgroundColor = 'rgba(255, 107, 0, 0.1)'

        // Remove highlight after 3 seconds
        setTimeout(() => {
          element.style.outline = originalOutline
          element.style.backgroundColor = originalBackground
        }, 3000)

        return {
          success: true,
          result: `Highlighted element: ${target}`,
        }
      }

      default:
        return {
          success: false,
          result: '',
          error: `Unknown action: ${action}`,
        }
    }
  } catch (error) {
    return {
      success: false,
      result: '',
      error: `Failed to execute action: ${error}`,
    }
  }
}

/**
 * Registry of client-side tools.
 */
export const clientTools: Record<string, ClientToolExecutor> = {
  read_page: readPage,
  get_element: getElement,
  execute_action: executeAction,
}

/**
 * Execute a client-side tool by name.
 */
export async function executeClientTool(
  toolName: string,
  arguments_: Record<string, unknown>
): Promise<ClientToolResult> {
  const executor = clientTools[toolName]

  if (!executor) {
    return {
      success: false,
      result: '',
      error: `Unknown client-side tool: ${toolName}`,
    }
  }

  return executor(arguments_)
}
