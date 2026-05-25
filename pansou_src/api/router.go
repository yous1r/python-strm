package api

import (
	"github.com/gin-gonic/gin"
	"pansou/config"
	"pansou/plugin"
	"pansou/service"
	"pansou/util"
)

// SetupRouter 设置路由
func SetupRouter(searchService *service.SearchService) *gin.Engine {
	// 设置搜索服务
	SetSearchService(searchService)
	
	// 设置为生产模式
	gin.SetMode(gin.ReleaseMode)
	
	// 创建默认路由
	r := gin.Default()
	
	// 添加中间件
	r.Use(CORSMiddleware())
	r.Use(LoggerMiddleware())
	r.Use(util.GzipMiddleware()) // 添加压缩中间件
	r.Use(AuthMiddleware())      // 添加认证中间件
	
	// 定义API路由组
	api := r.Group("/api")
	{
		// 认证接口（不需要认证，由中间件公开路径处理）
		auth := api.Group("/auth")
		{
			auth.POST("/login", LoginHandler)
			auth.POST("/verify", VerifyHandler)
			auth.POST("/logout", LogoutHandler)
		}
		
		// 搜索接口 - 支持POST和GET两种方式
		api.POST("/search", SearchHandler)
		api.GET("/search", SearchHandler) // 添加GET方式支持
		api.POST("/check/links", CheckHandler)
		
		// 动态修改配置接口 (热加载插件)
		api.POST("/config/plugins", func(c *gin.Context) {
			var req struct {
				EnabledPlugins []string `json:"enabled_plugins"`
			}
			if err := c.BindJSON(&req); err != nil {
				c.JSON(400, gin.H{"error": err.Error()})
				return
			}
			
			// 更新内存中的配置
			config.AppConfig.EnabledPlugins = req.EnabledPlugins
			
			// 清空并重新注册插件
			if searchService != nil && searchService.GetPluginManager() != nil {
				pm := searchService.GetPluginManager()
				pm.ClearPlugins()
				pm.RegisterGlobalPluginsWithFilter(req.EnabledPlugins)
			}
			
			c.JSON(200, gin.H{
				"status": "ok",
				"message": "Plugins reloaded successfully",
				"plugins": req.EnabledPlugins,
			})
		})
		
		// 健康检查接口
		api.GET("/health", func(c *gin.Context) {
			// 根据配置决定是否返回插件信息
			pluginCount := 0
			pluginNames := []string{}
			pluginsEnabled := config.AppConfig.AsyncPluginEnabled
			
			if pluginsEnabled && searchService != nil && searchService.GetPluginManager() != nil {
				plugins := searchService.GetPluginManager().GetPlugins()
				pluginCount = len(plugins)
				for _, p := range plugins {
					pluginNames = append(pluginNames, p.Name())
				}
			}
			
			// 获取频道信息
			channels := config.AppConfig.DefaultChannels
			channelsCount := len(channels)
			
			response := gin.H{
				"status":         "ok",
				"auth_enabled":   config.AppConfig.AuthEnabled, // 添加认证状态
				"plugins_enabled": pluginsEnabled,
				"channels":        channels,
				"channels_count":  channelsCount,
			}
			
			// 只有当插件启用时才返回插件相关信息
			if pluginsEnabled {
				response["plugin_count"] = pluginCount
				response["plugins"] = pluginNames
			}
			
			c.JSON(200, response)
		})
	}
	
	// 注册插件的Web路由（如果插件实现了PluginWithWebHandler接口）
	// 只有当插件功能启用且插件在启用列表中时才注册路由
	if config.AppConfig.AsyncPluginEnabled && searchService != nil && searchService.GetPluginManager() != nil {
		enabledPlugins := searchService.GetPluginManager().GetPlugins()
		for _, p := range enabledPlugins {
			if webPlugin, ok := p.(plugin.PluginWithWebHandler); ok {
				webPlugin.RegisterWebRoutes(r.Group(""))
			}
		}
	}
	
	return r
} 
